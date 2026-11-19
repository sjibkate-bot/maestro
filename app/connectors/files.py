"""File connector: Excel, CSV/TSV, JSON, PDF, plain text / email."""
import io
import json
import logging

import pandas as pd

from ..ai.provider import get_provider
from ..pipeline import ingest_dataframe, ingest_document, ingest_rows
from .sheets import ingest_workbook

log = logging.getLogger(__name__)


BRONZE_MAX_BYTES = 15 * 1024 * 1024


def _bronze_file(filename: str, data: bytes, source_id: str) -> dict:
    """Keep the file exactly as received (evidence of record) with its hash."""
    import hashlib
    from ..db import get_db
    from ..models import now
    digest = hashlib.sha256(data).hexdigest()
    doc = {"sha256": digest, "filename": filename, "source_id": source_id, "size": len(data),
           "received_at": now(), "content": data if len(data) <= BRONZE_MAX_BYTES else None}
    get_db().bronze_files.insert_one(doc)
    return {"sha256": digest, "size": len(data)}


def ingest_file(filename: str, data: bytes, source_id: str, record_type_hint: str | None = None) -> list[dict]:
    if not data:
        raise ValueError("empty file")
    _bronze_file(filename, data, source_id)
    name = filename.lower()
    results: list[dict] = []

    if name.endswith((".xlsx", ".xlsm")):
        results.extend(ingest_workbook(filename, data, source_id, record_type_hint))
    elif name.endswith(".xls"):
        sheets = pd.read_excel(io.BytesIO(data), sheet_name=None)
        for sheet, df in sheets.items():
            if df.dropna(how="all").empty:
                continue
            results.append(ingest_dataframe(df, source_id=source_id, source_type="excel",
                                            record_type_hint=record_type_hint,
                                            meta={"file": filename, "sheet": sheet}))
    elif name.endswith((".csv", ".tsv")):
        df = pd.read_csv(io.BytesIO(data), sep=None, engine="python")
        results.append(ingest_dataframe(df, source_id=source_id, source_type="csv",
                                        record_type_hint=record_type_hint, meta={"file": filename}))
    elif name.endswith(".json"):
        obj = json.loads(data)
        rows = find_records(obj)
        results.append(ingest_rows(flatten(rows), source_id=source_id, source_type="json",
                                   record_type_hint=record_type_hint, meta={"file": filename}))
    elif name.endswith(".pdf"):
        results.extend(_ingest_pdf(filename, data, source_id, record_type_hint))
    else:  # .txt, .eml, .md, anything textual
        text = data.decode("utf-8", errors="replace")
        results.append(ingest_document(source_id=source_id, source_type="document",
                                       text=text, filename=filename))
    return results


def _ingest_pdf(filename, data, source_id, record_type_hint):
    # With AI available, Claude reads the PDF directly: layout, tables, scans and
    # free text all at once. Without AI, fall back to table extraction.
    if get_provider().name != "rules":
        return [ingest_document(source_id=source_id, source_type="pdf",
                                pdf_bytes=data, filename=filename)]
    import pdfplumber

    out = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for i, page in enumerate(pdf.pages, 1):
            # Ruled tables only: loose-text detection turns scans and
            # whiteboards into junk rows. Those need the AI path.
            for table in page.extract_tables():
                if len(table) < 2:
                    continue
                header = [str(h or f"col{j}").strip() for j, h in enumerate(table[0])]
                df = pd.DataFrame(table[1:], columns=header)
                out.append(ingest_dataframe(df, source_id=source_id, source_type="pdf",
                                            record_type_hint=record_type_hint,
                                            meta={"file": filename, "page": i}))
    if not out:
        out.append({"file": filename, "records": 0,
                     "note": "no ruled tables found; scanned or free-form PDFs need AI (set ANTHROPIC_API_KEY)"})
    return out


def find_records(obj):
    """Locate the list of records inside an arbitrary JSON payload."""
    if isinstance(obj, list):
        return obj
    if isinstance(obj, dict):
        for key in ("value", "results", "data", "items", "records", "rows", "d"):
            if key in obj:
                return find_records(obj[key])
        lists = [v for v in obj.values() if isinstance(v, list) and v and isinstance(v[0], dict)]
        if lists:
            return max(lists, key=len)
        return [obj]
    return []


def flatten(rows: list) -> list[dict]:
    rows = [r for r in rows if isinstance(r, dict)]
    if not rows:
        return []
    df = pd.json_normalize(rows, sep=".")
    df = df[[c for c in df.columns if not c.startswith("__") and "@odata" not in c]]
    return df.to_dict(orient="records")
