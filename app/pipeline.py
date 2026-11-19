"""Ingestion pipeline: raw payload -> AI mapping/extraction -> canonical records -> MongoDB."""
import hashlib
import logging
import math
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

import pandas as pd

from .ai.claude import AIError
from .ai.provider import RuleBasedProvider, get_provider
from .db import get_db
from .models import DATE_FIELDS, ENTITIES, NUMERIC_FIELDS, MappingResult, now

log = logging.getLogger(__name__)
SAMPLE_ROWS = 8


def _clean(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    if isinstance(v, str):
        v = v.strip()
        return v or None
    if isinstance(v, pd.Timestamp):
        return None if pd.isna(v) else v.to_pydatetime()
    if hasattr(v, "item") and not isinstance(v, (str, bytes)):  # numpy scalar
        try:
            return v.item()
        except Exception:
            pass
    return v


def to_number(v: Any) -> Optional[float]:
    v = _clean(v)
    if v is None:
        return None
    if isinstance(v, bool):
        return float(v)
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).replace(",", "").replace("$", "").replace("€", "").replace("%", "")
    s = s.split()[0] if s.split() else s  # "12.4 hrs" -> "12.4"
    if s.startswith("(") and s.endswith(")"):
        s = "-" + s[1:-1]
    try:
        return float(s)
    except ValueError:
        return None


def to_date(v: Any) -> Optional[datetime]:
    v = _clean(v)
    if v is None:
        return None
    if isinstance(v, datetime):
        return v if v.year > 1900 else None  # Excel's 1899-12-29 "empty date"
    if hasattr(v, "year") and hasattr(v, "month") and hasattr(v, "day"):
        return datetime(v.year, v.month, v.day)
    if isinstance(v, (int, float)) and v > 1e9:  # epoch seconds / ms
        return datetime.fromtimestamp(v / 1000 if v > 1e12 else v, tz=timezone.utc)
    try:
        ts = pd.to_datetime(str(v), errors="coerce")
    except Exception:
        return None
    return None if pd.isna(ts) else ts.to_pydatetime()


def coerce(field: str, val: Any) -> Any:
    if field in NUMERIC_FIELDS:
        return to_number(val)
    if field in DATE_FIELDS:
        return to_date(val)
    val = _clean(val)
    return None if val is None else str(val)


def build_record(record_type: str, source_id: str, source_type: str,
                 fields: dict, attributes: Optional[dict] = None) -> dict:
    rec = {"record_type": record_type, "source_id": source_id, "source_type": source_type,
           "attributes": attributes or {}, "ingested_at": now()}
    for f, v in fields.items():
        rec[f] = coerce(f, v)
    return rec


def fingerprint(columns: Iterable[str]) -> str:
    key = "|".join(sorted(str(c).strip().lower() for c in columns))
    return hashlib.sha256(key.encode()).hexdigest()[:24]


def get_mapping(columns: list[str], sample: list[dict], record_type_hint: Optional[str] = None,
                context: str = "") -> MappingResult:
    """Return a column mapping, from cache if this table layout was seen before."""
    db = get_db()
    fp = fingerprint(columns) + (f":{record_type_hint}" if record_type_hint else "")
    cached = db.mappings.find_one({"fingerprint": fp})
    if cached:
        return MappingResult.model_validate(cached["mapping"])

    provider = get_provider()
    try:
        mapping = provider.map_columns(columns, sample, context)
        used = provider.name
    except AIError as e:
        log.warning("AI mapping failed, using rules: %s", e)
        mapping = RuleBasedProvider().map_columns(columns, sample, context)
        used = "rules"
    if record_type_hint and record_type_hint != mapping.record_type:
        # Re-map onto the type the user asked for.
        from .ai.provider import _map_for
        mapping = MappingResult(record_type=record_type_hint,
                                mappings=_map_for(record_type_hint, columns),
                                notes=f"Type forced to {record_type_hint}")
    db.mappings.update_one(
        {"fingerprint": fp},
        {"$set": {"fingerprint": fp, "columns": columns, "mapping": mapping.model_dump(mode="json"),
                  "provider": used, "created_at": now()}},
        upsert=True)
    return mapping


def _row_to_record(row: dict, mapping: MappingResult, source_id: str, source_type: str) -> dict:
    by_col = {m.source_column: m.canonical_field for m in mapping.mappings}
    fields, attrs = {}, {}
    for col, val in row.items():
        field = by_col.get(col)
        val = _clean(val)
        if field:
            fields[field] = val
        elif val is not None:
            attrs[str(col)] = val if isinstance(val, (int, float, bool, str, datetime)) else str(val)
    return build_record(mapping.record_type, source_id, source_type, fields, attrs)


def store(records: list[dict], raw: Any, source_id: str, source_type: str, meta: dict,
          replace_keys: Optional[list[dict]] = None) -> dict:
    """Persist a batch. `replace_keys` deletes older versions first (for snapshot-style sources)."""
    db = get_db()
    batch_id = uuid.uuid4().hex
    db.raw.insert_one({"batch_id": batch_id, "source_id": source_id, "source_type": source_type,
                       "meta": meta, "payload": raw, "received_at": now()})
    for q in replace_keys or []:
        db.records.delete_many(q)
    for r in records:
        r["batch_id"] = batch_id
    if records:
        db.records.insert_many(records)
    db.sources.update_one({"_id": source_id},
                          {"$set": {"last_ingested_at": now(), "source_type": source_type},
                           "$inc": {"record_count": len(records)}}, upsert=True)
    return {"batch_id": batch_id, "records": len(records), **meta}


def ingest_rows(rows: list[dict], *, source_id: str, source_type: str,
                record_type_hint: Optional[str] = None, meta: Optional[dict] = None,
                context: str = "") -> dict:
    rows = [{str(k): _clean(v) for k, v in r.items()} for r in rows]
    rows = [r for r in rows if any(v is not None for v in r.values())]
    if not rows:
        return store([], [], source_id, source_type, {**(meta or {}), "note": "no rows"})
    columns = list(dict.fromkeys(c for r in rows for c in r))
    mapping = get_mapping(columns, rows[:SAMPLE_ROWS], record_type_hint, context)
    records = [_row_to_record(r, mapping, source_id, source_type) for r in rows]
    info = {**(meta or {}), "record_type": mapping.record_type,
            "mapping": {m.source_column: m.canonical_field for m in mapping.mappings}}
    return store(records, rows, source_id, source_type, info)


def ingest_dataframe(df: pd.DataFrame, **kw) -> dict:
    df = df.dropna(how="all").dropna(axis=1, how="all")
    df.columns = [str(c).strip() for c in df.columns]
    return ingest_rows(df.to_dict(orient="records"), **kw)


def ingest_document(*, source_id: str, source_type: str, text: Optional[str] = None,
                    pdf_bytes: Optional[bytes] = None, filename: str = "") -> dict:
    provider = get_provider()
    extraction = provider.extract_document(text=text, pdf_bytes=pdf_bytes, filename=filename)
    doc_attrs = {"document": filename, "document_kind": extraction.document_kind}
    records = []
    for r in extraction.records:
        d = r.model_dump()
        rt = d.pop("record_type")
        rt = rt if rt in ENTITIES else "other"
        records.append(build_record(rt, source_id, source_type,
                                    {k: v for k, v in d.items() if v is not None}, dict(doc_attrs)))
    for a in extraction.assets:
        d = a.model_dump()
        fields = {"asset_id": d.pop("asset_id"), "location": d.pop("location"),
                  "status": d.get("current_gate")}
        records.append(build_record("asset_status", source_id, source_type, fields,
                                    {**doc_attrs, "board": d}))
    raw = {"filename": filename, "text": text[:20000] if text else None,
           "pdf_sha256": hashlib.sha256(pdf_bytes).hexdigest() if pdf_bytes else None}
    return store(records, raw, source_id, source_type,
                 {"document_kind": extraction.document_kind, "provider": provider.name,
                  "assets": len(extraction.assets)})
