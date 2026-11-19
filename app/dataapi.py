"""Generic data API for front ends: any entity, any industry.

Every endpoint works on the entity registry (core + registered + industry packs), so a new
kind of data is queryable as soon as it is loaded, with no new backend code.

* records(entity, ...)    filtered, sorted, paged rows; Gold by default
* summary(entity)         per-field profile for building filters and charts
* aggregate(entity, ...)  grouped metrics (count / sum / avg / min / max), optionally by time bucket
* record(id)              one record with its trust score and lineage back to the source row

Rows are flat: canonical fields, then extension / attribute values as "ext.<key>", then
"_meta" (id, source, grade, score, layer, loaded at). Dates are ISO strings.
"""
import re
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any, Optional

from bson import ObjectId

from .db import get_db
from .models import DATE_FIELDS, ENTITIES, NUMERIC_FIELDS

LAYERS = ("gold", "silver", "all")
MAX_PAGE = 500
OPS = {"eq", "ne", "gt", "gte", "lt", "lte", "in", "contains", "exists"}


def _entity(name: str) -> dict:
    from . import registry
    registry.ensure_loaded()
    if name not in ENTITIES or name == "other":
        raise KeyError(name)
    return ENTITIES[name]


def _base_query(entity: str, layer: str) -> dict:
    from . import governance
    if layer not in LAYERS:
        raise ValueError(f"layer must be one of {', '.join(LAYERS)}")
    q = {"record_type": entity}
    if layer == "gold" and governance.governed():
        return governance.gold_query(q)
    if layer == "silver":
        q["layer"] = {"$nin": ["quarantine", "history", "duplicate"]}
    return q


def _coerce(field: str, v: str) -> Any:
    from .pipeline import to_date, to_number
    if field in NUMERIC_FIELDS:
        n = to_number(v)
        return n if n is not None else v
    if field in DATE_FIELDS:
        d = to_date(v)
        return d if d is not None else v
    return v


def parse_filters(entity: str, params: dict[str, str]) -> dict:
    """field=value, field__op=value (op: ne gt gte lt lte in contains exists), ext.key=value."""
    fields = _entity(entity)
    q: dict = {}
    for raw, val in params.items():
        name, _, op = raw.partition("__")
        op = op or "eq"
        if op not in OPS:
            raise ValueError(f"unknown operator {op} in {raw}")
        if name.startswith("ext."):
            path, fld = "attributes." + name[4:], None
        elif name in fields:
            path, fld = name, name
        else:
            raise ValueError(f"{name} is not a field of {entity}")
        cv = (lambda x: _coerce(fld, x)) if fld else (lambda x: x)
        if op == "eq":
            cond = cv(val)
        elif op == "in":
            cond = {"$in": [cv(x.strip()) for x in val.split(",")]}
        elif op == "contains":
            cond = {"$regex": re.escape(val), "$options": "i"}
        elif op == "exists":
            cond = {"$exists": val.lower() in ("1", "true", "yes")}
        else:
            cond = {"$" + op: cv(val)}
        if path in q and isinstance(q[path], dict) and isinstance(cond, dict):
            q[path].update(cond)
        else:
            q[path] = cond
    return q


def _iso(v):
    return v.isoformat() if isinstance(v, datetime) else v


def flat(r: dict, fields: dict) -> dict:
    row = {f: _iso(r.get(f)) for f in fields}
    for k, v in (r.get("attributes") or {}).items():
        row[f"ext.{k}"] = _iso(v)
    t = r.get("trust") or {}
    row["_meta"] = {"id": str(r["_id"]), "source_id": r.get("source_id"), "layer": r.get("layer", "silver"),
                    "grade": t.get("grade"), "score": t.get("score"), "loaded_at": _iso(r.get("ingested_at")),
                    "batch_id": r.get("batch_id")}
    return row


def records(entity: str, *, filters: Optional[dict] = None, q: Optional[str] = None, sort: Optional[str] = None,
            page: int = 1, page_size: int = 50, layer: str = "gold", fields: Optional[list[str]] = None) -> dict:
    ef = _entity(entity)
    query = {**_base_query(entity, layer), **(parse_filters(entity, filters) if filters else {})}
    if q:   # free text over text fields
        tf = [f for f in ef if f not in NUMERIC_FIELDS and f not in DATE_FIELDS]
        query["$or"] = [{f: {"$regex": re.escape(q), "$options": "i"}} for f in tf] or [{"_id": None}]
    page, page_size = max(1, page), max(1, min(page_size, MAX_PAGE))
    order = []
    for part in (sort or "").split(","):
        part = part.strip()
        if part:
            fld = part.lstrip("-")
            if fld not in ef and not fld.startswith("ext."):
                raise ValueError(f"cannot sort by {fld}")
            order.append(("attributes." + fld[4:] if fld.startswith("ext.") else fld, -1 if part.startswith("-") else 1))
    db = get_db()
    cur = db.records.find(query)
    if order:
        cur = cur.sort(order)
    total = db.records.count_documents(query)
    rows = [flat(r, ef) for r in cur.skip((page - 1) * page_size).limit(page_size)]
    if fields:
        keep = set(fields) | {"_meta"}
        rows = [{k: v for k, v in r.items() if k in keep} for r in rows]
    return {"entity": entity, "layer": layer, "page": page, "page_size": page_size, "total": total,
            "pages": (total + page_size - 1) // page_size, "rows": rows}


def summary(entity: str, layer: str = "gold") -> dict:
    """Per-field profile: filled %, distinct count, numeric min/max/avg, date range, top values."""
    ef = _entity(entity)
    docs = list(get_db().records.find(_base_query(entity, layer), {f: 1 for f in ef} | {"attributes": 1, "trust": 1}))
    n = len(docs)
    out = []
    for f, (kind, desc) in ef.items():
        vals = [d.get(f) for d in docs if d.get(f) is not None and d.get(f) != ""]
        info = {"field": f, "type": {"number": "number", "date": "date"}.get(kind, "text"), "description": desc,
                "filled_pct": round(100 * len(vals) / n, 1) if n else None, "distinct": len({str(v) for v in vals})}
        nums = [v for v in vals if isinstance(v, (int, float)) and not isinstance(v, bool)]
        dates = [v for v in vals if isinstance(v, datetime)]
        if nums:
            info.update({"min": min(nums), "max": max(nums), "avg": round(sum(nums) / len(nums), 4), "sum": round(sum(nums), 4)})
        if dates:
            info.update({"min": min(dates).isoformat(), "max": max(dates).isoformat()})
        if not nums and not dates:
            info["top"] = [{"value": v, "count": c} for v, c in Counter(str(v) for v in vals).most_common(10)]
        out.append(info)
    ext = Counter(k for d in docs for k in (d.get("attributes") or {}))
    grades = Counter((d.get("trust") or {}).get("grade") for d in docs)
    return {"entity": entity, "layer": layer, "records": n, "fields": out,
            "extensions": [{"key": k, "filled_pct": round(100 * c / n, 1)} for k, c in ext.most_common()] if n else [],
            "grades": {k: v for k, v in grades.items() if k}}


BUCKETS = {"day": "%Y-%m-%d", "week": "%G-W%V", "month": "%Y-%m", "quarter": None, "year": "%Y"}
METRICS = {"count", "sum", "avg", "min", "max", "distinct"}


def aggregate(entity: str, *, group_by: Optional[list[str]] = None, metric: str = "count", field: Optional[str] = None,
              date_field: Optional[str] = None, bucket: Optional[str] = None, filters: Optional[dict] = None,
              layer: str = "gold", limit: int = 200) -> dict:
    """Grouped metric for charts, e.g. sum of quantity by location, or count by month of order_date."""
    ef = _entity(entity)
    if metric not in METRICS:
        raise ValueError(f"metric must be one of {', '.join(sorted(METRICS))}")
    if metric != "count" and not field:
        raise ValueError(f"{metric} needs a field")
    if field and field not in ef and not field.startswith("ext."):
        raise ValueError(f"{field} is not a field of {entity}")
    if metric in ("sum", "avg") and field in ef and field not in NUMERIC_FIELDS:
        raise ValueError(f"{field} is not numeric")
    group_by = [g for g in (group_by or []) if g]
    for g in group_by:
        if g not in ef and not g.startswith("ext."):
            raise ValueError(f"cannot group by {g}")
    if bucket and (bucket not in BUCKETS or not date_field):
        raise ValueError("bucket needs date_field and one of " + ", ".join(BUCKETS))
    if date_field and date_field not in DATE_FIELDS:
        raise ValueError(f"{date_field} is not a date field")
    query = {**_base_query(entity, layer), **(parse_filters(entity, filters) if filters else {})}
    get = lambda d, k: (d.get("attributes") or {}).get(k[4:]) if k.startswith("ext.") else d.get(k)
    acc: dict[tuple, list] = defaultdict(list)
    for d in get_db().records.find(query):
        key = tuple(str(get(d, g)) if get(d, g) is not None else None for g in group_by)
        if bucket:
            dv = d.get(date_field)
            if not isinstance(dv, datetime):
                continue
            b = f"{dv.year}-Q{(dv.month - 1) // 3 + 1}" if bucket == "quarter" else dv.strftime(BUCKETS[bucket])
            key = key + (b,)
        acc[key].append(get(d, field) if field else 1)
    rows = []
    for key, vals in acc.items():
        nums = [v for v in vals if isinstance(v, (int, float)) and not isinstance(v, bool)]
        val = {"count": len(vals), "sum": sum(nums) if nums else None, "avg": (sum(nums) / len(nums)) if nums else None,
               "min": min(nums) if nums else None, "max": max(nums) if nums else None,
               "distinct": len({str(v) for v in vals if v is not None})}[metric]
        row = {g: key[i] for i, g in enumerate(group_by)}
        if bucket:
            row[bucket] = key[-1]
        row["value"] = round(val, 4) if isinstance(val, float) else val
        row["n"] = len(vals)
        rows.append(row)
    rows.sort(key=lambda r: (r.get(bucket) or "") if bucket else -(r["value"] or 0))
    return {"entity": entity, "layer": layer, "metric": metric, "field": field, "group_by": group_by,
            "bucket": bucket, "date_field": date_field, "rows": rows[:max(1, min(limit, 5000))], "groups": len(rows)}


def record(rid: str) -> dict:
    from . import governance
    try:
        oid = ObjectId(rid)
    except Exception:
        raise KeyError(rid)
    r = get_db().records.find_one({"_id": oid})
    if not r:
        raise KeyError(rid)
    out = flat(r, ENTITIES.get(r["record_type"], {}))
    out["_entity"] = r["record_type"]
    out["_trust"] = r.get("trust")
    try:
        out["_lineage"] = governance.record_detail(rid).get("lineage")
    except Exception:
        out["_lineage"] = None
    return out
