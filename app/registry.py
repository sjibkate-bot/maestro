"""Entity registry: the data model as data, so any industry's data can be read, governed
and served without code changes.

* Core entities are the ones defined in models.ENTITIES (supply chain, MRO/depot, risk).
* Registered entities are stored in the `entity_registry` collection, created by a person
  (from Mapping Studio, from an industry pack, or through the API), versioned, and applied
  at runtime: they join ENTITIES, the field-type sets, the column synonyms used for mapping,
  and the governance rules (required fields, natural key, identity keys, allowed values,
  domain, data product).

Definition (what POST /api/entities accepts):
{
  "name": "work_order",                      # snake_case, unique
  "label": "Work order", "description": "...",
  "domain": "manufacturing",                 # groups entities for governance and the UI
  "pack": "manufacturing",                   # optional: industry pack it came from
  "fields": [
     {"name": "work_order_id", "type": "text", "description": "...", "synonyms": ["wo", "aufnr"],
      "required": true, "allowed": null, "min": null, "max": null},
     ...],
  "key": ["work_order_id"],                  # natural key: newer loads replace older versions
  "key_mode": "snapshot",                    # snapshot (keep newest) or event (every row is an event)
  "freshness_field": "updated_at", "freshness_days": 30
}
"""
import copy
import re
from typing import Optional

from .db import get_db
from .models import ALL_FIELDS, DATE_FIELDS, ENTITIES, NUMERIC_FIELDS, D, N, S, now

TYPES = {"text": S, "number": N, "date": D, "boolean": S}
KIND_TO_TYPE = {S: "text", N: "number", D: "date"}
CORE = frozenset(ENTITIES)            # entities defined in code; everything else is registered
NAME = re.compile(r"^[a-z][a-z0-9_]{1,40}$")
_applied: dict[str, dict] = {}        # name -> definition currently applied in this process


def _norm(s) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def snake(s: str) -> str:
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", str(s).strip())
    s = re.sub(r"[^a-zA-Z0-9]+", "_", s).strip("_").lower()
    if s and s[0].isdigit():
        s = "f_" + s
    return s[:40] or "field"


# ------------------------------------------------------------------ validation

def validate(defn: dict, *, existing: Optional[dict] = None) -> list[str]:
    errs = []
    name = defn.get("name") or ""
    if not NAME.match(name):
        errs.append("name must be snake_case: lowercase letters, digits and _, starting with a letter")
    if name in CORE:
        errs.append(f"{name} is a core entity; extend it with Extension fields in Mapping Studio, or choose another name")
    fields = defn.get("fields") or []
    if not fields:
        errs.append("at least one field is needed")
    seen = set()
    for f in fields:
        fn, ft = f.get("name") or "", f.get("type") or "text"
        if not NAME.match(fn):
            errs.append(f"field {fn!r}: use snake_case")
            continue
        if fn in seen:
            errs.append(f"field {fn} appears twice")
        seen.add(fn)
        if ft not in TYPES:
            errs.append(f"field {fn}: type must be one of {', '.join(TYPES)}")
            continue
        # a field name has one type across the whole model, because values are typed by name
        known = ALL_FIELDS.get(fn)
        if known and known[0] != TYPES[ft] and not (existing and fn in {x["name"] for x in existing.get("fields", [])}):
            errs.append(f"field {fn}: already used as {KIND_TO_TYPE[known[0]]} elsewhere; rename it (e.g. {fn}_{ft}) or use that type")
        if f.get("allowed") is not None and not isinstance(f["allowed"], list):
            errs.append(f"field {fn}: allowed must be a list of values")
    key = defn.get("key") or []
    for k in key:
        if k not in seen:
            errs.append(f"key field {k} is not one of the fields")
    if defn.get("key_mode", "snapshot") not in ("snapshot", "event"):
        errs.append("key_mode must be snapshot or event")
    if defn.get("domain") and not NAME.match(defn["domain"]):
        errs.append("domain must be snake_case")
    if existing:   # a new version may add fields, but not change the type of a field that has data
        old = {x["name"]: x for x in existing.get("fields", [])}
        has_data = get_db().records.count_documents({"record_type": name}, limit=1) > 0
        for f in fields:
            o = old.get(f.get("name"))
            if o and o.get("type") != f.get("type") and has_data:
                errs.append(f"field {f['name']}: type cannot change from {o['type']} while records exist")
        if has_data:
            removed = set(old) - seen
            if removed:
                errs.append("fields cannot be removed while records exist: " + ", ".join(sorted(removed)))
    return errs


# ------------------------------------------------------------------ apply to the running model

def _apply(defn: dict) -> None:
    from .ai import provider
    name = defn["name"]
    ENTITIES[name] = {f["name"]: (TYPES[f.get("type", "text")], f.get("description") or f["name"].replace("_", " "))
                      for f in defn["fields"]}
    for f in defn["fields"]:
        kind = TYPES[f.get("type", "text")]
        ALL_FIELDS.setdefault(f["name"], (kind, f.get("description") or ""))
        (NUMERIC_FIELDS if kind == N else DATE_FIELDS if kind == D else set()).add(f["name"])
        syns = [_norm(f["name"])] + [_norm(x) for x in f.get("synonyms") or [] if _norm(x)]
        cur = provider._SYNONYMS.setdefault(f["name"], [])
        for s_ in syns:
            if s_ not in cur:
                cur.append(s_)
            lk = provider._LOOKUP.setdefault(s_, [])
            if f["name"] not in lk:
                lk.append(f["name"])
    _applied[name] = defn


def _unapply(name: str) -> None:
    ENTITIES.pop(name, None)
    _applied.pop(name, None)


def load() -> int:
    """Apply every active registered entity (called at start-up and after changes)."""
    n = 0
    active = {d["name"]: d for d in get_db().entity_registry.find({"status": "ACTIVE"})}
    for name in list(_applied):
        if name not in active:
            _unapply(name)
    for d in active.values():
        _apply(d)
        n += 1
    return n


def ensure_loaded() -> None:
    """Cheap check so every worker sees entities registered by another."""
    db = get_db()
    names = {d["name"] for d in db.entity_registry.find({"status": "ACTIVE"}, {"name": 1})}
    if names != set(_applied):
        load()


# ------------------------------------------------------------------ CRUD

def _clean_def(defn: dict) -> dict:
    d = copy.deepcopy(defn)
    d["name"] = d.get("name", "").strip()
    d["label"] = d.get("label") or d["name"].replace("_", " ").capitalize()
    d["domain"] = d.get("domain") or "general"
    d["key_mode"] = d.get("key_mode") or "snapshot"
    for f in d.get("fields", []):
        f["type"] = f.get("type") or "text"
        f["synonyms"] = [str(x) for x in f.get("synonyms") or []]
        f["required"] = bool(f.get("required"))
    if not d.get("key"):
        d["key"] = [f["name"] for f in d.get("fields", []) if f.get("key")][:3]
    return d


def create(defn: dict, user: str) -> dict:
    from . import governance
    if not (user or "").strip():
        raise ValueError("Say who is registering the entity")
    d = _clean_def(defn)
    db = get_db()
    existing = db.entity_registry.find_one({"name": d["name"], "status": "ACTIVE"})
    errs = validate(d, existing=existing)
    if errs:
        raise ValueError("; ".join(errs))
    version = (existing["version"] + 1) if existing else 1
    if existing:
        db.entity_registry.update_one({"_id": existing["_id"]}, {"$set": {"status": "SUPERSEDED", "superseded_at": now()}})
    doc = {**d, "version": version, "status": "ACTIVE", "created_by": user, "created_at": now()}
    doc.pop("_id", None)
    db.entity_registry.insert_one(doc)
    load()
    governance.audit("entity_registered" if version == 1 else "entity_versioned", user,
                     {"entity": d["name"], "version": version, "fields": len(d["fields"]), "domain": d["domain"]})
    return describe(d["name"])


def retire(name: str, user: str) -> dict:
    from . import governance
    db = get_db()
    cur = db.entity_registry.find_one({"name": name, "status": "ACTIVE"})
    if not cur:
        raise KeyError(name)
    if db.records.count_documents({"record_type": name}, limit=1):
        raise ValueError(f"{name} has records: delete its sources first")
    db.entity_registry.update_one({"_id": cur["_id"]}, {"$set": {"status": "RETIRED", "retired_at": now(), "retired_by": user}})
    load()
    governance.audit("entity_retired", user, {"entity": name})
    return {"name": name, "status": "RETIRED"}


def definition(name: str) -> Optional[dict]:
    d = get_db().entity_registry.find_one({"name": name, "status": "ACTIVE"}, {"_id": 0})
    return d


def describe(name: str) -> dict:
    """One entity as the front end needs it: fields with types, rules and where its data is."""
    from . import governance
    if name not in ENTITIES:
        raise KeyError(name)
    cfg = governance.config()
    d = definition(name)
    req = {x if isinstance(x, str) else x[0] for x in cfg["required"].get(name, [])}
    key = (cfg["keys"].get(name) or (None, []))[1]
    allowed = {k.split(".", 1)[1] if "." in k else k: v for k, v in cfg["enums"].items()
               if (k.startswith(name + ".")) or ("." not in k and name in CORE and k in ENTITIES[name])}
    extra = {f["name"]: f for f in (d or {}).get("fields", [])}
    fields = [{"name": f, "type": KIND_TO_TYPE[k], "description": desc, "required": f in req, "key": f in key,
               "allowed": allowed.get(f), "synonyms": (extra.get(f) or {}).get("synonyms", [])}
              for f, (k, desc) in ENTITIES[name].items()]
    db = get_db()
    return {"name": name, "label": (d or {}).get("label") or name.replace("_", " ").capitalize(),
            "description": (d or {}).get("description"), "origin": "core" if name in CORE else "registered",
            "domain": governance.domain_of(name, cfg), "pack": (d or {}).get("pack"),
            "version": (d or {}).get("version"), "key": key, "key_mode": (cfg["keys"].get(name) or ("snapshot", []))[0],
            "fields": fields, "records": db.records.count_documents({"record_type": name}),
            "created_by": (d or {}).get("created_by"), "created_at": (d or {}).get("created_at")}


def catalog() -> list[dict]:
    from . import governance
    ensure_loaded()
    cfg = governance.config()
    db = get_db()
    counts = {}
    for r in db.records.aggregate([{"$group": {"_id": "$record_type", "n": {"$sum": 1}}}]):
        counts[r["_id"]] = r["n"]
    defs = {d["name"]: d for d in db.entity_registry.find({"status": "ACTIVE"}, {"_id": 0})}
    out = []
    for name in sorted(ENTITIES):
        if name == "other":
            continue
        d = defs.get(name, {})
        out.append({"name": name, "label": d.get("label") or name.replace("_", " ").capitalize(),
                    "domain": governance.domain_of(name, cfg), "origin": "core" if name in CORE else "registered",
                    "pack": d.get("pack"), "fields": len(ENTITIES[name]), "records": counts.get(name, 0)})
    return out


# ------------------------------------------------------------------ governance rules for registered entities

def merge_governance(cfg: dict) -> dict:
    """Add the rules of registered entities to a governance config (called by governance.config)."""
    ensure_loaded()
    for name, d in _applied.items():
        req = [f["name"] for f in d["fields"] if f.get("required")]
        if req:
            cfg["required"].setdefault(name, req)
        if d.get("key"):
            cfg["keys"].setdefault(name, (d.get("key_mode", "snapshot"), list(d["key"])))
            cfg["hard_keys"].setdefault(name, list(d["key"]))
        for f in d["fields"]:
            if f.get("allowed"):
                cfg["enums"].setdefault(f"{name}.{f['name']}", list(f["allowed"]))
            if f.get("min") is not None or f.get("max") is not None:
                cfg.setdefault("ranges", {}).setdefault(f"{name}.{f['name']}", [f.get("min"), f.get("max")])
        if d.get("freshness_field"):
            cfg["freshness"].setdefault(name, (d["freshness_field"], int(d.get("freshness_days") or 30)))
        dom = d.get("domain") or "general"
        dd = cfg["domains"].setdefault(dom, {"entities": [], "owner": f"{dom.replace('_', ' ').title()} Data Owner",
                                             "steward": f"{dom.replace('_', ' ').title()} Data Steward", "sensitivity": "Internal"})
        if name not in dd["entities"]:
            dd["entities"].append(name)
    # one data product per registered domain that has none
    covered = {e for p in cfg["products"] for e in p.get("entities", [])}
    by_dom: dict[str, list[str]] = {}
    for name, d in _applied.items():
        if name not in covered:
            by_dom.setdefault(d.get("domain") or "general", []).append(name)
    for dom, ents in by_dom.items():
        pid = f"{dom}_data"
        p = next((x for x in cfg["products"] if x["id"] == pid), None)
        if p:
            p["entities"] = sorted(set(p["entities"]) | set(ents))
        else:
            cfg["products"].append({"id": pid, "name": f"{dom.replace('_', ' ').title()} Data", "domain": dom,
                                    "description": f"Registered {dom.replace('_', ' ')} entities: " + ", ".join(sorted(ents)),
                                    "entities": sorted(ents), "freshness_days": 30, "registered": True})
    return cfg


# ------------------------------------------------------------------ propose a definition from a source object

def propose_from_object(obj: dict, name: Optional[str] = None, domain: Optional[str] = None) -> dict:
    """Draft an entity from a profiled source: one field per column, typed from the values;
    columns with no empty values are required; the first fully distinct column is the key."""
    base = name or snake(re.sub(r"\.[a-z0-9]+$", "", (obj.get("sheet") or obj.get("file") or "entity").lower()))
    base = re.sub(r"^\d+_", "", base) or "entity"
    if base in CORE or not NAME.match(base):
        base = ("x_" + base)[:40] if base in CORE else snake(base)
    fields, used = [], set()
    typemap = {"number": "number", "date": "date", "boolean": "boolean"}
    for f in obj["fields"]:
        fn = snake(f["name"])
        if fn in used:
            fn = f"{fn}_{f['ordinal']}"
        used.add(fn)
        ft = typemap.get(f.get("type"), "text")
        known = ALL_FIELDS.get(fn)
        if known and known[0] != TYPES[ft]:
            fn = f"{fn}_{ft}"
        fields.append({"name": fn, "type": ft, "description": f"From source column '{f['name']}'",
                       "synonyms": [f["name"]] if _norm(f["name"]) != _norm(fn) else [],
                       "required": f.get("null_pct", 100) == 0, "source_column": f["name"]})
    key_col = next((f for f in obj["fields"] if f.get("unique_pct") == 100 and f.get("null_pct") == 0 and f.get("type") == "text"), None) \
        or next((f for f in obj["fields"] if f.get("unique_pct") == 100 and f.get("null_pct") == 0), None)
    key = [next(x["name"] for x in fields if x["source_column"] == key_col["name"])] if key_col else []
    return {"name": base, "label": base.replace("_", " ").capitalize(), "domain": domain or "general",
            "description": f"Registered from {obj.get('name')}", "fields": fields, "key": key, "key_mode": "snapshot",
            "proposal_notes": [
                "Types come from the values; check numbers that are really codes (e.g. zip codes) and set them to text.",
                f"Key: {key[0]} has a distinct value on every row." if key else "No column is distinct on every row: pick a key, or use key_mode event.",
                "Required = no empty values in this file; relax it if other files may leave it empty."]}
