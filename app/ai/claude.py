"""Claude-backed AI provider.

Three jobs:
  0. detect_layout    - find where the real table(s) sit in a messy spreadsheet
                        (titles, data dictionaries, side-by-side tables, forms).
  1. map_columns      - look at a table's headers + sample rows, decide what kind of
                        data it is and map each column to the canonical schema.
                        Called once per new table layout, then cached.
  2. extract_document - read an unstructured document (PDF, scanned PDF, email,
                        text) and pull out structured records.
  3. answer           - answer a business question from computed KPIs + samples.
"""
import base64
import json
from typing import Type, TypeVar

import anthropic
from pydantic import BaseModel

from .. import config
from ..models import (ENTITIES, AnalystAnswer, DocumentExtraction,
                      MappingResult, SheetLayout)

T = TypeVar("T", bound=BaseModel)

# Server-side refusal fallbacks: if the model declines, the API retries on a
# fallback model within the same call.
_BETAS = ["server-side-fallback-2026-07-01"]


class AIError(RuntimeError):
    pass


class ClaudeProvider:
    name = "claude"

    def __init__(self, model: str | None = None):
        self.model = model or config.AI_MODEL
        self.client = anthropic.Anthropic()

    # ---------- low-level ----------
    def _call(self, content, schema: Type[T], system: str, effort: str) -> T:
        try:
            with self.client.beta.messages.stream(
                model=self.model,
                max_tokens=64000,
                system=system,
                messages=[{"role": "user", "content": content}],
                output_format=schema,
                output_config={"effort": effort},
                betas=_BETAS,
                fallbacks="default",
            ) as stream:
                msg = stream.get_final_message()
        except anthropic.APIStatusError as e:
            raise AIError(f"Claude API error {e.status_code}: {e.message}") from e
        except anthropic.APIConnectionError as e:
            raise AIError(f"Cannot reach Claude API: {e}") from e

        if msg.stop_reason == "refusal":
            raise AIError("The model declined this request.")
        if msg.stop_reason == "max_tokens":
            raise AIError("Response truncated (max_tokens). Split the document into smaller parts.")
        text = next((b.text for b in msg.content if b.type == "text"), None)
        if text is None:
            raise AIError("No text in model response.")
        return schema.model_validate_json(text)

    # ---------- jobs ----------
    def detect_layout(self, grid: list[list]) -> SheetLayout:
        system = (
            "You read spreadsheet sheets exported from operational systems and hand-built "
            "trackers. Find the data table(s): the header row and column span of each. "
            "Ignore titles, notes and data-dictionary blocks that describe columns (they are "
            "context, not data). If two tables sit side by side, list both. If the sheet is a "
            "form or report (labels next to values, no repeating rows), return kind='form'."
        )
        rows = "\n".join(f"{i}: " + json.dumps(r, ensure_ascii=False, default=str) for i, r in enumerate(grid[:60]))
        return self._call(f"Sheet rows (0-based index: cell values):\n{rows}", SheetLayout, system, effort="low")

    def map_columns(self, columns: list[str], sample_rows: list[dict], context: str = "") -> MappingResult:
        entities = "\n".join(
            f"- {name}: " + ", ".join(f"{f} ({d})" for f, (_, d) in fields.items())
            for name, fields in ENTITIES.items() if fields)
        system = (
            "You are a data engineer for supply chain and maintenance operations. You map "
            "columns from arbitrary sources (Excel trackers, ERP tables such as SAP/Oracle/"
            "Dynamics, maintenance systems, CSV files, API payloads, IoT feeds) onto a "
            "canonical schema. Column names may be abbreviated, in another language, or ERP "
            "technical names (MATNR, WERKS, EBELN, LABST, EQUNR). Use the sample values, not "
            "just the headers. First pick the record type, then map each column to a field "
            "of that type, using each field at most once; leave a column unmapped (null) "
            "when nothing fits - unmapped columns are kept as extra attributes."
        )
        prompt = (
            f"Record types and their fields:\n{entities}\n\n"
            f"Source columns: {json.dumps(columns, ensure_ascii=False)}\n\n"
            f"Sample rows:\n{json.dumps(sample_rows, ensure_ascii=False, default=str, indent=1)}\n\n"
            + (f"Notes found near the table (may describe the columns):\n{context}\n\n" if context else "")
            + "Return the record type and a mapping for every source column."
        )
        result = self._call(prompt, MappingResult, system, effort="low")
        if result.record_type not in ENTITIES:
            result.record_type = "other"
        valid = set(ENTITIES[result.record_type])
        for m in result.mappings:
            if m.canonical_field not in valid:
                m.canonical_field = None
        return result

    def extract_document(self, *, text: str | None = None, pdf_bytes: bytes | None = None,
                         filename: str = "") -> DocumentExtraction:
        system = (
            "You extract structured data from business documents. Two kinds:\n"
            "1. Supply-chain documents (purchase orders, invoices, packing lists, bills of "
            "lading, delivery notes, inventory reports, quotes, emails): put one entry per "
            "line item in `records`; record_type is one of inventory, purchase_order, "
            "sales_order, shipment, supplier, product. Repeat document-level values (order "
            "number, supplier, dates) on each line.\n"
            "2. Maintenance / production status boards (whiteboards, at-a-glance charts, "
            "dock boards, often photographed or scanned with handwriting): put one entry per "
            "aircraft or asset in `assets`. Read 'Day: 44/43' as day_in_gate=44, "
            "gate_goal_days=43 and 'Flow Day 181/239' as flow_day=181, flow_goal_days=239. "
            "List crossed-out items in notes prefixed with 'done: '.\n"
            "Use null for anything not stated and never invent values. Dates as ISO 8601; "
            "when a date has no year, infer it from dates elsewhere on the document."
        )
        content: list[dict] = []
        if pdf_bytes is not None:
            content.append({
                "type": "document",
                "source": {"type": "base64", "media_type": "application/pdf",
                           "data": base64.standard_b64encode(pdf_bytes).decode()},
            })
        if text is not None:
            if len(text) > config.MAX_DOC_CHARS:
                raise AIError(
                    f"Document is {len(text):,} characters, above MAX_DOC_CHARS="
                    f"{config.MAX_DOC_CHARS:,}. Split it or raise the limit.")
            content.append({"type": "text", "text": f"<document name=\"{filename}\">\n{text}\n</document>"})
        content.append({"type": "text", "text": "Extract every record and every asset from this document."})
        return self._call(content, DocumentExtraction, system, effort="medium")

    def answer(self, question: str, context: dict) -> AnalystAnswer:
        system = (
            "You are a senior supply-chain and depot-maintenance analyst. Answer using only "
            "the data provided (KPIs, risk lists, gate performance, aircraft status, alerts "
            "and record samples from the organisation's own systems). Quote concrete numbers, "
            "tail numbers, SKUs and supplier names. If the data cannot answer "
            "the question, say what data is missing. Keep the answer under 250 words."
        )
        prompt = (
            f"<data>\n{json.dumps(context, ensure_ascii=False, default=str, indent=1)}\n</data>\n\n"
            f"Question: {question}"
        )
        return self._call(prompt, AnalystAnswer, system, effort="high")
