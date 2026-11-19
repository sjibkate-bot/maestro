"""Mapping Studio: staged ingestion, field-level mapping decisions, mapping profiles
and the validate -> govern -> publish step.

Flow
----
1. Upload one or more files as an ingestion batch (POST /api/batches). Every table
   (CSV, sheet, JSON list) becomes a *source object*: its rows are kept in Bronze,
   each column is profiled (type, nulls, distinct values, samples) and the object is
   classified to an entity with alternatives and per-column suggestions.
2. Decide each column in Mapping Studio: Accept a MAESTRO field (must belong to the
   object's entity), Ignore it (stays in Bronze only) or keep it as an Extension under
   a named key. Columns left undecided with no match are kept as attributes.
3. Save & activate a mapping profile. The next file with the same columns is mapped
   by the profile automatically. A file whose columns differ from its active profile
   is held as drift for review: the profile is never changed silently.
4. Validate, govern and publish: objects whose required fields are resolved become
   Silver/Gold records through the normal governance run. Every run is kept.

Template parsers (PDM tail sheets, machine info) and documents read by the AI are
not tabular mappings; they are published directly and listed in the batch as such.
"""
import contextvars
import hashlib
import re
import uuid
from collections import Counter
from datetime import datetime
from typing import Any, Optional

from .db import get_db
from .models import DATE_FIELDS, ENTITIES, NUMERIC_FIELDS, now

STAGING: contextvars.ContextVar[Optional[dict]] = contextvars.ContextVar("staging", default=None)

AUTO, SUGGESTED, REVIEW, UNMAPPED = "AUTO", "SUGGESTED", "REVIEW", "UNMAPPED"
ACCEPTED, IGNORED, EXTENSION = "ACCEPTED", "IGNORED", "EXTENSION"
DECIDED = {ACCEPTED, IGNORED, EXTENSION}
MAPS_TO_FIELD = {AUTO, ACCEPTED}            # statuses that put the value in a canonical field
NEEDS_DECISION = {SUGGESTED, REVIEW}        # publishing waits for these
SAMPLE_ROWS = 12

# object-name hints (file or sheet name) per entity; a match adds to the entity score
NAME_HINTS = {
    "supplier": ["supplier", "vendor", "supplynetwork", "suppliernetwork", "network"],
    "supply_link": ["relationship", "supplylink", "edges", "links", "supplyrelationships"],
    "country_risk": ["countryrisk", "country"], "transport_lane": ["lane", "route", "transport"],
    "risk_item": ["riskregister", "risks", "register"], "alert": ["alert"], "scenario": ["scenario"],
    "data_source": ["sourcecatalog", "datasource", "catalog", "catalogue"],
    "inventory": ["inventory", "stock", "onhand"], "purchase_order": ["purchaseorder", "openpo", "po"],
    "forecast": ["forecast", "demand"], "sales_order": ["salesorder", "orders"], "product": ["item", "material", "product"],
    "shipment": ["shipment", "asn", "delivery"], "bom_line": ["bom", "billofmaterial"], "work_task": ["task", "script"],
    "fault": ["fault", "squawk"], "sensor_reading": ["telemetry", "sensor", "iot"],
}


GENERIC_TOKENS = {"date", "name", "code", "number", "num", "qty", "value", "type", "status", "amount", "total",
                  "time", "day", "days", "count", "flag", "level", "description", "text", "the", "and", "for"}


def _norm(s: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def _tokens(s: Any) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", re.sub(r"([a-z])([A-Z])", r"\1 \2", str(s)).lower()) if len(t) >= 3}


def _synonyms() -> dict[str, list[str]]:
    from .ai.provider import _SYNONYMS
    return _SYNONYMS


def required_fields(entity: str) -> list[str]:
    """Fields an object must map before it can be published (required + identity keys)."""
    from . import governance
    cfg = governance.config()
    out = []
    for f in cfg["required"].get(entity, []) + cfg["hard_keys"].get(entity, []):
        f = f[0] if isinstance(f, list) else f   # alternatives: the first is enough to name it
        if f not in out:
            out.append(f)
    return out


# ------------------------------------------------------------------ profiling & classification

def _type_of(values: list) -> str:
    vals = [v for v in values if v is not None and str(v).strip() != ""]
    if not vals:
        return "empty"
    from .pipeline import to_date, to_number
    nums = sum(1 for v in vals if to_number(v) is not None and not isinstance(v, datetime))
    if nums / len(vals) >= .9:
        return "number"
    dates = sum(1 for v in vals if isinstance(v, datetime) or (isinstance(v, str) and re.search(r"\d{4}-\d{1,2}-\d{1,2}|\d{1,2}/\d{1,2}/\d{2,4}", v) and to_date(v)))
    if dates / len(vals) >= .9:
        return "date"
    if {str(v).strip().lower() for v in vals} <= {"true", "false", "yes", "no", "y", "n", "0", "1"}:
        return "boolean"
    return "text"


def profile_columns(columns: list[str], rows: list[dict]) -> list[dict]:
    out = []
    n = max(len(rows), 1)
    for i, c in enumerate(columns):
        vals = [r.get(c) for r in rows]
        filled = [v for v in vals if v is not None and str(v).strip() != ""]
        uniq = len({str(v) for v in filled})
        samples = list(dict.fromkeys(str(v)[:40] for v in filled))[:5]
        out.append({"name": c, "ordinal": i + 1, "type": _type_of(vals), "null_pct": round(100 * (n - len(filled)) / n, 1),
                    "unique_pct": round(100 * uniq / max(len(filled), 1), 1), "samples": samples})
    return out


def _type_fits(field: str, col_type: str) -> bool:
    if col_type == "empty":
        return True
    if field in NUMERIC_FIELDS:
        return col_type in ("number", "boolean")
    if field in DATE_FIELDS:
        return col_type == "date"
    return True


def candidates(column: str, col_type: str, entity: str) -> list[dict]:
    """Ranked MAESTRO fields of `entity` for one source column."""
    syn = _synonyms()
    nc, tc = _norm(column), _tokens(column)
    out = []
    for f in list(ENTITIES.get(entity, {})):
        names = [f] + syn.get(f, [])
        if nc in {_norm(x) for x in names}:
            score, why = 100, "name matches a known synonym"
        else:
            ft = set().union(*[_tokens(x) for x in names] or [set()]) | {t for x in names for t in [_norm(x)] if len(t) >= 4}
            common = {t for t in tc if t in ft or any(len(t) >= 4 and len(x) >= 4 and (t in x or x in t)
                                                      and min(len(t), len(x)) / max(len(t), len(x)) >= .7 for x in ft)}
            if not common:
                continue
            specific = common - GENERIC_TOKENS
            score = (78 if len(tc) <= 2 else 66) if specific else 60   # "date" or "code" alone is weak evidence
            why = f"shares '{sorted(specific or common)[0]}' with {f}"
        if not _type_fits(f, col_type):
            score -= 25
            why += f"; values look like {col_type}"
        if score >= 55:
            out.append({"field": f, "score": score, "why": why})
    out.sort(key=lambda x: -x["score"])
    return out[:5]


def status_for(score: Optional[float]) -> str:
    if score is None or score <= 0:
        return UNMAPPED
    return AUTO if score >= 90 else SUGGESTED if score >= 75 else REVIEW if score >= 60 else UNMAPPED


def classify(name: str, fields: list[dict], ai_entity: Optional[str] = None) -> dict:
    """Score every entity: coverage of its required fields, how well the columns map,
    and the object name. Returns the best entity, alternatives and per-column suggestions."""
    from . import registry
    registry.ensure_loaded()
    nn = _norm(name)
    scored = []
    for entity in list(ENTITIES):
        if entity == "other":
            continue
        sugg = {f["name"]: candidates(f["name"], f["type"], entity) for f in fields}
        best = {c: (s[0] if s else None) for c, s in sugg.items()}
        used, picks = set(), {}
        for c, b in sorted(best.items(), key=lambda kv: -(kv[1]["score"] if kv[1] else 0)):
            pick = next((x for x in sugg[c] if x["field"] not in used), None)
            if pick:
                used.add(pick["field"])
                picks[c] = pick
        if not picks:
            continue
        req = required_fields(entity)
        strong = {p["field"] for p in picks.values() if p["score"] >= 75}
        coverage = sum(1 for r in req if r in strong) / len(req) if req else .5
        fit = sum(p["score"] for p in picks.values()) / (100 * max(len(fields), 1))
        hint = 1 if any(h in nn for h in NAME_HINTS.get(entity, [])) else 0
        conf = min(99, round(55 * fit + 30 * coverage + 15 * hint))
        if ai_entity == entity:
            conf = max(conf, 90)
        scored.append({"entity": entity, "confidence": conf, "picks": picks, "suggestions": sugg,
                       "coverage": round(coverage, 2), "name_hint": bool(hint)})
    scored.sort(key=lambda x: -x["confidence"])
    if not scored:
        return {"entity": "other", "confidence": 0, "alternatives": [], "picks": {}, "suggestions": {}}
    best = scored[0]
    # a weak match is worse than none: unknown data should be registered, not forced into a near entity
    known = best["confidence"] >= 55 and best["coverage"] >= .5
    return {"entity": best["entity"] if known else "other", "confidence": best["confidence"],
            "alternatives": [{"entity": x["entity"], "confidence": x["confidence"]} for x in scored[1:4]],
            "picks": best["picks"] if known else {}, "suggestions": best["suggestions"] if known else {},
            "evidence": {"required_coverage": best["coverage"], "name_hint": best["name_hint"]}}


def _apply_entity(obj_fields: list[dict], entity: str, picks: dict, suggestions: dict) -> None:
    for f in obj_fields:
        p = picks.get(f["name"])
        f.update({"canonical_field": p["field"] if p else None, "confidence": p["score"] if p else 0,
                  "status": status_for(p["score"] if p else 0), "why": p["why"] if p else "no field of this entity matches",
                  "candidates": suggestions.get(f["name"], []), "extension_key": None,
                  "decided_by": None, "decided_at": None})


# ------------------------------------------------------------------ profiles

def active_profile(fp: str) -> Optional[dict]:
    return get_db().mapping_profiles.find_one({"fingerprint": fp, "status": "ACTIVE"})


def _profile_for_name(object_key: str) -> Optional[dict]:
    return get_db().mapping_profiles.find_one({"object_key": object_key, "status": "ACTIVE"})


def object_key(filename: str, sheet: Optional[str]) -> str:
    """Stable name of a source across loads: file name without dates/versions, plus sheet."""
    stem = re.sub(r"\.[a-z0-9]+$", "", (filename or "").lower())
    stem = re.sub(r"[_\- ]?(\d{4}[-_]?\d{2}[-_]?\d{2}|\d{8}|v\d+)$", "", stem)
    return stem + (f"#{sheet.lower()}" if sheet else "")


def _from_profile(obj_fields: list[dict], profile: dict) -> None:
    pf = profile["fields"]
    for f in obj_fields:
        d = pf.get(f["name"])
        if d:
            f.update({"canonical_field": d.get("canonical_field"), "status": d["status"], "extension_key": d.get("extension_key"),
                      "confidence": 100 if d["status"] in DECIDED | {AUTO} else 0, "why": f"active profile v{profile['version']}",
                      "decided_by": d.get("decided_by"), "decided_at": d.get("decided_at")})
        else:
            f.update({"canonical_field": None, "status": UNMAPPED, "confidence": 0, "extension_key": None,
                      "why": "new column: not in the active profile", "decided_by": None, "decided_at": None})


# ------------------------------------------------------------------ staging

def begin_batch(name: str, user: str, mode: str = "review") -> dict:
    b = {"_id": uuid.uuid4().hex, "name": name, "created_at": now(), "created_by": user or "unknown",
         "mode": mode, "files": [], "status": "DISCOVERING"}
    get_db().ingest_batches.insert_one(b)
    return b


def stage_rows(rows: list[dict], columns: list[str], *, source_id: str, source_type: str, meta: dict,
               context: str = "", record_type_hint: Optional[str] = None) -> dict:
    """Called by the pipeline for every table while a batch is being staged."""
    st = STAGING.get()
    db = get_db()
    from .pipeline import fingerprint
    fp = fingerprint(columns)
    fname, sheet = meta.get("file") or source_id, meta.get("sheet")
    key = object_key(fname, sheet)
    fields = profile_columns(columns, rows)
    obj = {"_id": uuid.uuid4().hex, "batch_id": st["batch_id"], "source_id": source_id, "source_type": source_type,
           "file": fname, "sheet": sheet, "name": (sheet and f"{fname} · {sheet}") or fname, "object_key": key,
           "fingerprint": fp, "rows": len(rows), "columns": len(columns), "meta": meta, "context": context,
           "created_at": now(), "fields": fields, "drift": None, "profile_id": None, "published": None}
    prof = active_profile(fp)
    if prof:
        obj.update({"entity": prof["entity"], "confidence": 100, "alternatives": [], "profile_id": prof["_id"],
                    "classification": {"profile_reuse": True, "profile_version": prof["version"]}})
        _from_profile(fields, prof)
    else:
        ai_entity = None
        if not record_type_hint:
            try:   # AI (when configured) proposes the entity; rules still give alternatives and evidence
                from .ai.provider import get_provider
                prov = get_provider()
                if prov.name != "rules":
                    ai_entity = prov.map_columns(columns, rows[:8], context).record_type
            except Exception:
                ai_entity = None
        cl = classify(fname + " " + (sheet or ""), fields, ai_entity)
        entity = record_type_hint or cl["entity"]
        if record_type_hint and record_type_hint != cl["entity"]:
            sugg = {f["name"]: candidates(f["name"], f["type"], entity) for f in fields}
            picks = {c: s[0] for c, s in sugg.items() if s}
            cl = {**cl, "picks": picks, "suggestions": sugg}
        obj.update({"entity": entity, "confidence": cl["confidence"], "alternatives": cl["alternatives"],
                    "classification": {"evidence": cl.get("evidence"), "ai_entity": ai_entity, "forced": bool(record_type_hint)}})
        _apply_entity(fields, entity, cl["picks"], cl["suggestions"])
        old = _profile_for_name(key)
        if old:   # same source, different columns: drift. Keep earlier decisions for columns that remain.
            prev = set(old["fields"])
            if old["entity"] == entity:
                kept = [f for f in fields if f["name"] in prev]
                _from_profile(kept, old)
            obj["drift"] = {"profile_id": old["_id"], "profile_version": old["version"],
                            "added": [c for c in columns if c not in prev], "removed": [c for c in prev if c not in columns]}
    obj["status"] = _object_status(obj)
    db.staged_rows.insert_many([{"object_id": obj["_id"], "i": i, "row": r} for i, r in enumerate(rows)]) if rows else None
    db.source_objects.insert_one(obj)
    out = {"object_id": obj["_id"], "file": fname, "sheet": sheet, "entity": obj["entity"], "status": obj["status"],
           "records": 0, "staged": len(rows)}
    # an active profile with no drift needs no review: publish straight away in review mode too
    if st.get("mode") == "auto" or (prof and obj["status"] == "READY"):
        out.update(publish_object(obj["_id"], st.get("user") or "profile reuse", run_id=st.get("run_id")))
    return out


def _gate(obj: dict) -> list[str]:
    """Reasons the object cannot be published yet (empty = ready)."""
    reasons = []
    if obj.get("entity") in (None, "other"):
        reasons.append("Entity not recognised: choose one, or register a new entity from this source")
    mapped = {f["canonical_field"] for f in obj["fields"] if f["status"] in MAPS_TO_FIELD and f["canonical_field"]}
    missing = [r for r in required_fields(obj.get("entity") or "") if r not in mapped]
    if missing:
        reasons.append("Required field(s) not mapped: " + ", ".join(missing))
    pending = [f["name"] for f in obj["fields"] if f["status"] in NEEDS_DECISION]
    if pending:
        reasons.append(f"{len(pending)} suggestion(s) to accept or reject: " + ", ".join(pending[:6]))
    dup = [k for k, n in Counter(f["canonical_field"] for f in obj["fields"] if f["status"] in MAPS_TO_FIELD and f["canonical_field"]).items() if n > 1]
    if dup:
        reasons.append("Same MAESTRO field used twice: " + ", ".join(dup))
    if obj.get("drift") and not obj.get("drift_reviewed"):
        reasons.append("Columns changed since the active profile: review and save a new profile version")
    return reasons


def _object_status(obj: dict) -> str:
    if obj.get("published"):
        return "PUBLISHED"
    if obj.get("drift") and not obj.get("drift_reviewed"):
        return "DRIFT"
    return "READY" if not _gate(obj) else "MAPPING_REQUIRED"


def _save(obj: dict) -> dict:
    obj["status"] = _object_status(obj)
    get_db().source_objects.replace_one({"_id": obj["_id"]}, obj)
    return obj


def get_object(object_id: str) -> dict:
    obj = get_db().source_objects.find_one({"_id": object_id})
    if not obj:
        raise KeyError(object_id)
    return obj


def object_view(object_id: str) -> dict:
    obj = get_object(object_id)
    rows = [r["row"] for r in get_db().staged_rows.find({"object_id": object_id}).sort("i", 1).limit(SAMPLE_ROWS)]
    ent = obj.get("entity") or "other"
    req = required_fields(ent)
    mapped = {f["canonical_field"] for f in obj["fields"] if f["status"] in MAPS_TO_FIELD and f["canonical_field"]}
    prof = get_db().mapping_profiles.find_one({"_id": obj.get("profile_id")}) if obj.get("profile_id") else None
    return {**{k: v for k, v in obj.items() if k != "context"}, "gate": _gate(obj), "sample_rows": rows,
            "entity_fields": [{"field": f, "description": d, "required": f in req, "mapped": f in mapped}
                              for f, (_, d) in ENTITIES.get(ent, {}).items()],
            "entities": sorted(e for e in list(ENTITIES) if e != "other"),
            "profile": {k: prof[k] for k in ("_id", "version", "status", "activated_at", "activated_by")} if prof else None,
            "decisions": list(get_db().mapping_decisions.find({"object_id": object_id}, {"_id": 0}).sort("at", -1).limit(50))}


# ------------------------------------------------------------------ decisions

def _decide_log(obj, field, action, user, detail):
    from . import governance
    get_db().mapping_decisions.insert_one({"object_id": obj["_id"], "batch_id": obj["batch_id"], "field": field,
                                           "action": action, "user": user, "at": now(), **detail})
    governance.audit("mapping_" + action.lower(), user, {"object": obj["name"], "field": field, **detail})


def propose_entity(object_id: str, name: Optional[str] = None, domain: Optional[str] = None) -> dict:
    from . import registry
    return registry.propose_from_object(get_object(object_id), name, domain)


def register_entity(object_id: str, definition: dict, user: str) -> dict:
    """Register a new entity for a source nothing matched, then map the source to it 1:1."""
    from . import registry
    obj = get_object(object_id)
    if obj.get("published"):
        raise ValueError("Already published: load the source again to remap")
    desc = registry.create(definition, user)
    cols = {f.get("source_column"): f["name"] for f in definition.get("fields", []) if f.get("source_column")}
    obj = set_entity(object_id, desc["name"], user)
    for f in obj["fields"]:   # the proposal maps each column to the field made from it
        target = cols.get(f["name"])
        if target and target in ENTITIES[desc["name"]]:
            f.update({"canonical_field": target, "status": ACCEPTED, "confidence": 100, "extension_key": None,
                      "why": "field created from this column", "decided_by": user, "decided_at": now()})
    _decide_log(obj, None, "REGISTER_ENTITY", user, {"entity": desc["name"], "version": desc["version"]})
    _save(obj)
    return desc


def set_entity(object_id: str, entity: str, user: str) -> dict:
    if entity not in ENTITIES or entity == "other":
        raise ValueError(f"Unknown entity {entity}")
    obj = get_object(object_id)
    if obj.get("published"):
        raise ValueError("Already published: delete the source or load it again to remap")
    sugg = {f["name"]: candidates(f["name"], f["type"], entity) for f in obj["fields"]}
    used, picks = set(), {}
    for f in obj["fields"]:
        p = next((x for x in sugg[f["name"]] if x["field"] not in used), None)
        if p:
            used.add(p["field"])
            picks[f["name"]] = p
    _apply_entity(obj["fields"], entity, picks, sugg)
    obj["entity"], obj["confidence"] = entity, 100
    obj.setdefault("classification", {})["confirmed_by"] = user
    _decide_log(obj, None, "ENTITY", user, {"entity": entity})
    return _save(obj)


def field_action(object_id: str, column: str, action: str, user: str, canonical_field: Optional[str] = None,
                 extension_key: Optional[str] = None) -> dict:
    action = (action or "").upper()
    if action == "CONFIRMED":   # older clients
        action = ACCEPTED
    if action not in (ACCEPTED, IGNORED, EXTENSION, "RESET"):
        raise ValueError("Action must be ACCEPTED, IGNORED, EXTENSION or RESET")
    if not (user or "").strip():
        raise ValueError("Say who is deciding (name or role)")
    obj = get_object(object_id)
    if obj.get("published"):
        raise ValueError("Already published: load the source again to change its mapping")
    f = next((x for x in obj["fields"] if x["name"] == column), None)
    if not f:
        raise KeyError(column)
    entity = obj.get("entity")
    if action == ACCEPTED:
        target = canonical_field or f.get("canonical_field")
        if not target:
            raise ValueError("Choose a MAESTRO field to accept")
        if target not in ENTITIES.get(entity, {}):
            raise ValueError(f"{target} is not a field of {entity}")
        clash = next((x["name"] for x in obj["fields"] if x is not f and x["status"] in MAPS_TO_FIELD and x["canonical_field"] == target), None)
        if clash:
            raise ValueError(f"{target} is already mapped from {clash}: ignore or change that column first")
        f.update({"canonical_field": target, "status": ACCEPTED, "confidence": 100, "extension_key": None})
    elif action == IGNORED:
        f.update({"canonical_field": None, "status": IGNORED, "confidence": 0, "extension_key": None})
    elif action == EXTENSION:
        key = re.sub(r"[^a-zA-Z0-9_]", "_", (extension_key or f["name"]).strip()).strip("_").lower()
        if not key:
            raise ValueError("Extension key is required")
        if key in ENTITIES.get(entity, {}):
            raise ValueError(f"{key} is a MAESTRO field of {entity}: accept it instead")
        f.update({"canonical_field": None, "status": EXTENSION, "confidence": 100, "extension_key": key})
    else:   # RESET to the suggestion
        p = (f.get("candidates") or [None])[0]
        f.update({"canonical_field": p["field"] if p else None, "status": status_for(p["score"] if p else 0),
                  "confidence": p["score"] if p else 0, "extension_key": None})
    f.update({"decided_by": None if action == "RESET" else user, "decided_at": None if action == "RESET" else now()})
    _decide_log(obj, column, action, user, {"canonical_field": f["canonical_field"], "extension_key": f["extension_key"]})
    return _save(obj)


def accept_high_confidence(object_id: str, user: str, minimum: int = 90) -> dict:
    obj = get_object(object_id)
    n = 0
    for f in obj["fields"]:
        if f["status"] in (AUTO, SUGGESTED) and f["canonical_field"] and f["confidence"] >= minimum:
            try:
                obj = field_action(object_id, f["name"], ACCEPTED, user, f["canonical_field"])
                n += 1
            except ValueError:
                pass
    return {"accepted": n, "object": get_object(object_id)}


def save_profile(object_id: str, user: str) -> dict:
    if not (user or "").strip():
        raise ValueError("Say who is activating the profile")
    obj = get_object(object_id)
    if obj.get("entity") in (None, "other"):
        raise ValueError("Choose an entity first")
    pending = [f["name"] for f in obj["fields"] if f["status"] in NEEDS_DECISION]
    if pending:
        raise ValueError("Decide these first: " + ", ".join(pending))
    db = get_db()
    last = db.mapping_profiles.find_one({"object_key": obj["object_key"]}, sort=[("version", -1)])
    version = (last["version"] + 1) if last else 1
    db.mapping_profiles.update_many({"$or": [{"object_key": obj["object_key"]}, {"fingerprint": obj["fingerprint"]}], "status": "ACTIVE"},
                                    {"$set": {"status": "SUPERSEDED", "superseded_at": now()}})
    prof = {"_id": uuid.uuid4().hex, "object_key": obj["object_key"], "name": obj["name"], "fingerprint": obj["fingerprint"],
            "entity": obj["entity"], "version": version, "status": "ACTIVE", "activated_by": user, "activated_at": now(),
            "fields": {f["name"]: {"status": f["status"] if f["status"] != UNMAPPED else UNMAPPED,
                                   "canonical_field": f["canonical_field"] if f["status"] in MAPS_TO_FIELD else None,
                                   "extension_key": f.get("extension_key"), "decided_by": f.get("decided_by"),
                                   "decided_at": f.get("decided_at")} for f in obj["fields"]},
            "source_object": obj["_id"]}
    db.mapping_profiles.insert_one(prof)
    obj["profile_id"], obj["drift_reviewed"] = prof["_id"], True
    _decide_log(obj, None, "PROFILE", user, {"version": version})
    _save(obj)
    return prof


def profiles() -> list[dict]:
    return list(get_db().mapping_profiles.find({}, {"fields": 0}).sort([("object_key", 1), ("version", -1)]))


# ------------------------------------------------------------------ row conversion & publish

def convert_row(row: dict, fields: list[dict]) -> tuple[dict, dict]:
    """Canonical fields and extension attributes for one row. Ignored columns are dropped
    (they stay in Bronze); undecided columns with no match are kept as attributes."""
    from .pipeline import _clean
    by = {f["name"]: f for f in fields}
    data, attrs = {}, {}
    for col, val in row.items():
        f = by.get(col)
        val = _clean(val)
        if f is None:
            if val is not None:
                attrs[str(col)] = val
            continue
        st = f["status"]
        if st in MAPS_TO_FIELD and f.get("canonical_field"):
            data[f["canonical_field"]] = val
        elif st == IGNORED:
            continue
        elif val is not None:
            attrs[f.get("extension_key") or str(col)] = val if isinstance(val, (int, float, bool, str, datetime)) else str(val)
    return data, attrs


def publish_object(object_id: str, user: str, run_id: Optional[str] = None) -> dict:
    from .pipeline import build_record, store
    obj = get_object(object_id)
    reasons = _gate(obj)
    if reasons:
        return {"object_id": object_id, "published": False, "reasons": reasons, "records": 0}
    rows = [r["row"] for r in get_db().staged_rows.find({"object_id": object_id}).sort("i", 1)]
    recs = []
    for r in rows:
        data, attrs = convert_row(r, obj["fields"])
        recs.append(build_record(obj["entity"], obj["source_id"], obj["source_type"], data, attrs))
    mapping = {f["name"]: (f["canonical_field"] if f["status"] in MAPS_TO_FIELD else
                           f"ext:{f['extension_key'] or f['name']}" if f["status"] in (EXTENSION, UNMAPPED) else None)
               for f in obj["fields"]}
    for r in recs:
        r["object_id"] = object_id
    res = store(recs, rows, obj["source_id"], obj["source_type"],
                {**obj["meta"], "record_type": obj["entity"], "mapping": mapping, "object_id": object_id,
                 "profile_id": obj.get("profile_id"), "publish_run": run_id, "published_by": user})
    db = get_db()
    layers = Counter(r.get("layer", "silver") for r in db.records.find({"batch_id": res["batch_id"]}, {"layer": 1}))
    grades = Counter((r.get("trust") or {}).get("grade") for r in db.records.find({"batch_id": res["batch_id"]}, {"trust": 1}))
    obj["published"] = {"at": now(), "by": user, "store_batch": res["batch_id"], "records": res["records"],
                        "layers": dict(layers), "grades": {k: v for k, v in grades.items() if k}, "run_id": run_id}
    _save(obj)
    return {"object_id": object_id, "published": True, "records": res["records"], "store_batch": res["batch_id"],
            "layers": dict(layers), "grades": obj["published"]["grades"]}


def batch_view(batch_id: str) -> dict:
    db = get_db()
    b = db.ingest_batches.find_one({"_id": batch_id})
    if not b:
        raise KeyError(batch_id)
    objs = list(db.source_objects.find({"batch_id": batch_id}, {"context": 0}))
    for o in objs:
        o["gate"] = [] if o.get("direct") else _gate(o)   # template sheets and documents have no mapping step
        o["pending"] = sum(1 for f in o["fields"] if f["status"] in NEEDS_DECISION)
        o["decided"] = sum(1 for f in o["fields"] if f["status"] in DECIDED)
        o["fields"] = len(o["fields"])
    counts = Counter(o["status"] for o in objs)
    status = ("PUBLISHED" if objs and counts.get("PUBLISHED") == len(objs) else
              "READY" if objs and counts.get("PUBLISHED", 0) + counts.get("READY", 0) == len(objs) else
              "MAPPING_REQUIRED" if objs else b.get("status"))
    runs = list(db.publish_runs.find({"batch_id": batch_id}).sort("started_at", -1).limit(10))
    return {**b, "status": status, "objects": objs, "counts": dict(counts),
            "rows": sum(o["rows"] for o in objs), "runs": runs}


def batches(limit: int = 30) -> list[dict]:
    db = get_db()
    out = []
    for b in db.ingest_batches.find().sort("created_at", -1).limit(limit):
        objs = list(db.source_objects.find({"batch_id": b["_id"]}, {"status": 1, "rows": 1}))
        c = Counter(o["status"] for o in objs)
        out.append({**b, "objects": len(objs), "rows": sum(o["rows"] for o in objs), "counts": dict(c)})
    return out


def publish_batch(batch_id: str, user: str) -> dict:
    """Validate, govern and publish every object of a batch that is ready. Each stage
    reports what actually happened; the run is kept whether it succeeds or not."""
    from . import governance
    if not (user or "").strip():
        raise ValueError("Say who is publishing (name or role)")
    db = get_db()
    b = db.ingest_batches.find_one({"_id": batch_id})
    if not b:
        raise KeyError(batch_id)
    run = {"_id": uuid.uuid4().hex, "batch_id": batch_id, "requested_by": user, "started_at": now(),
           "status": "RUNNING", "stages": [], "objects": []}
    db.publish_runs.insert_one(run)

    def stage(name, status, detail, **extra):
        run["stages"].append({"stage": name, "status": status, "detail": detail, "at": now(), **extra})
        db.publish_runs.replace_one({"_id": run["_id"]}, run)

    try:
        objs = list(db.source_objects.find({"batch_id": batch_id}))
        todo = [o for o in objs if not o.get("published")]
        blocked = [(o, _gate(o)) for o in todo if _gate(o)]
        ready = [o for o in todo if not _gate(o)]
        stage("VALIDATE", "PASSED" if not blocked else ("PARTIAL" if ready else "FAILED"),
              f"{len(ready)} of {len(todo)} object(s) ready" + (f"; {len(blocked)} held: mapping not complete" if blocked else ""),
              held=[{"object": o["name"], "reasons": r} for o, r in blocked])
        results = []
        for o in ready:
            try:
                results.append({"object": o["name"], **publish_object(o["_id"], user, run["_id"])})
            except Exception as e:   # one bad object must not stop the others
                results.append({"object": o["name"], "object_id": o["_id"], "published": False, "error": str(e)})
        run["objects"] = results + [{"object": o["name"], "object_id": o["_id"], "published": False, "reasons": r} for o, r in blocked]
        ok = [r for r in results if r.get("published")]
        recs = sum(r["records"] for r in ok)
        layers = Counter()
        grades = Counter()
        for r in ok:
            layers.update(r.get("layers") or {})
            grades.update(r.get("grades") or {})
        failed = [r for r in results if not r.get("published")]
        stage("GOVERN", "PASSED" if ok and not failed else ("FAILED" if not ok and (failed or ready) else "SKIPPED"),
              f"{recs} record(s) scored: " + ", ".join(f"{n} {k}" for k, n in sorted(grades.items())) +
              (f"; {layers.get('quarantine', 0)} quarantined" if layers.get("quarantine") else "") +
              (f"; {len(failed)} object(s) failed" if failed else ""),
              layers=dict(layers), grades=dict(grades))
        affected = {o["entity"] for o in ready}
        prods = [p for p in governance.products() if set(p.get("entities", [])) & affected]
        cert = [{"product": p["name"], "certification": p.get("certification"), "grade": p.get("grade"),
                 "score": p.get("score")} for p in prods]
        stage("CERTIFY", "PENDING_OWNER" if any(c["certification"] != "CERTIFIED" for c in cert) else "CERTIFIED",
              "Data products affected: " + (", ".join(f"{c['product']} ({c['certification'] or 'not certified'})" for c in cert) or "none") +
              ". Certification is a named owner's decision in Governance.", products=cert)
        stage("PUBLISH", "PASSED" if ok else "SKIPPED",
              f"{len(ok)} object(s) published to Silver; Gold = grade A/B, current, not quarantined")
        risk_note, risk_status = "No supply data in this batch", "SKIPPED"
        if affected & {"supplier", "purchase_order", "inventory", "forecast", "sales_order", "supply_link",
                       "risk_item", "alert", "scenario", "country_risk", "transport_lane", "product"}:
            from . import supply_risk
            ov = supply_risk.overview()
            k = ov["kpis"]
            risk_status = "PASSED"
            risk_note = f"Supply risk refreshed: {k['suppliers']} suppliers, {k['high'] + k['critical']} high or critical, {k['low_cover']} items under 14 days of cover"
        stage("RISK", risk_status, risk_note)
        run["status"] = "COMPLETED" if ok and not blocked and not failed else "PARTIAL" if ok else "FAILED"
        run["records"] = recs
    except Exception as e:
        run["status"], run["error"] = "FAILED", str(e)
        stage("ERROR", "FAILED", str(e))
    run["completed_at"] = now()
    db.publish_runs.replace_one({"_id": run["_id"]}, run)
    governance.audit("publish_run", user, {"batch": batch_id, "status": run["status"], "records": run.get("records", 0)})
    return run


def delete_for_source(source_id: Optional[str]) -> None:
    db = get_db()
    if source_id is None:
        for c in ("source_objects", "staged_rows", "ingest_batches", "publish_runs", "mapping_decisions"):
            db[c].delete_many({})
        return
    ids = [o["_id"] for o in db.source_objects.find({"source_id": source_id}, {"_id": 1})]
    db.staged_rows.delete_many({"object_id": {"$in": ids}})
    db.source_objects.delete_many({"_id": {"$in": ids}})


def register_direct(batch_id: str, filename: str, source_id: str, results: list[dict]) -> None:
    """Template sheets and AI-read documents are published without a mapping step: list them in the batch."""
    db = get_db()
    for r in results or []:
        if r.get("object_id") or not r.get("batch_id"):
            continue
        db.source_objects.insert_one({
            "_id": uuid.uuid4().hex, "batch_id": batch_id, "source_id": source_id, "source_type": "direct",
            "file": filename, "sheet": r.get("sheet"), "name": (r.get("sheet") and f"{filename} · {r['sheet']}") or filename,
            "object_key": object_key(filename, r.get("sheet")), "fingerprint": None, "rows": r.get("records", 0),
            "columns": 0, "meta": {}, "fields": [], "entity": r.get("record_type") or r.get("document_kind") or "document",
            "confidence": 100, "alternatives": [], "direct": r.get("template") or "document", "status": "PUBLISHED",
            "published": {"at": now(), "by": "template parser", "store_batch": r["batch_id"], "records": r.get("records", 0)},
            "created_at": now()})


def finish_batch(batch_id: str, files: list[dict], load: list[dict]) -> None:
    errors = [x for x in load if x.get("error")]
    get_db().ingest_batches.update_one({"_id": batch_id}, {"$set": {"files": files, "status": "DISCOVERED",
                                                                    "errors": errors, "discovered_at": now()}})


def awaiting_summary() -> dict:
    """Loaded but not yet published: tables staged in Mapping Studio. Governance shows this so a
    product with no data says why (waiting for review) instead of looking empty."""
    db = get_db()
    objs = list(db.source_objects.find({"published": None, "direct": {"$exists": False}},
                                       {"fields": 1, "batch_id": 1, "name": 1, "entity": 1, "rows": 1, "drift": 1, "drift_reviewed": 1}))
    by_batch: dict[str, dict] = {}
    by_entity = Counter()
    for o in objs:
        st = _object_status(o)
        b = by_batch.setdefault(o["batch_id"], {"batch_id": o["batch_id"], "objects": 0, "rows": 0, "status": Counter()})
        b["objects"] += 1
        b["rows"] += o.get("rows") or 0
        b["status"][st] += 1
        by_entity[o.get("entity") or "other"] += o.get("rows") or 0
    names = {b["_id"]: b for b in db.ingest_batches.find({"_id": {"$in": list(by_batch)}}, {"name": 1, "created_at": 1, "created_by": 1})}
    batches_ = []
    for bid, b in by_batch.items():
        meta = names.get(bid, {})
        batches_.append({**b, "status": dict(b["status"]), "name": meta.get("name"), "created_at": meta.get("created_at"),
                         "created_by": meta.get("created_by")})
    batches_.sort(key=lambda x: x.get("created_at") or now(), reverse=True)
    return {"objects": len(objs), "rows": sum(o.get("rows") or 0 for o in objs),
            "ready": sum(b["status"].get("READY", 0) for b in batches_),
            "needs_mapping": sum(b["status"].get("MAPPING_REQUIRED", 0) + b["status"].get("DRIFT", 0) for b in batches_),
            "batches": batches_, "rows_by_entity": dict(by_entity)}
