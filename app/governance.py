"""Data governance after ingest: Bronze -> Silver -> Gold, Data Trust Score, quarantine,
certified data products, the AI decision gate and the monitoring loop.

Follows the DFSP IP Data Governance design:

* Bronze  - exact copy of what each source sent (files + rows, with hashes). Measure,
            never fix. Gate 1 (technical checks) decides whether rows move up.
* Silver  - records cleaned and conformed, duplicates removed, matched to master data,
            and every record scored on six dimensions into a Data Trust Score (A-D).
            Grade D or a hard-rule failure -> quarantine with a reason code, a steward
            and an SLA. Records are never edited in place; fixes come from the source.
* Gold    - data products built only from grade A/B records. Gate 2: product grade
            >= B *and* the data owner certifies. The trust label travels with the data.
* Decisions - the AI may recommend only when data grade and model confidence allow.

Thresholds, weights and roles are proposals until a governance council ratifies them,
so they live in an editable config document.
"""
import copy
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from statistics import mean, pstdev
from typing import Any, Optional

from .db import get_db
from .models import ENTITIES, NUMERIC_FIELDS, now

# ---------------------------------------------------------------- configuration

DEFAULT_CONFIG: dict[str, Any] = {
    "version": 1,
    "weights": {"completeness": 20, "validity": 20, "timeliness": 20,
                "consistency": 20, "uniqueness": 10, "provenance": 10},
    "grades": {"A": 95, "B": 85, "C": 70},          # D below C
    "confidence": {"high": 0.80, "medium": 0.60},   # low below medium
    "quarantine_sla_days": 5,
    "drift_threshold_pct": 25,
    "gold_target_pct": 95,
    "asset_id_pattern": r"^\d{2}-\d{4,5}$",
    "domains": {
        "depot": {"entities": ["asset_flow", "gate_event", "gate_plan", "production_schedule"],
                  "owner": "AMXG Production Superintendent", "steward": "Depot Scheduling Steward",
                  "sensitivity": "CUI"},
        "fleet": {"entities": ["asset_status", "subsystem_health", "sensor_reading", "fault"],
                  "owner": "Fleet Reliability Lead", "steward": "Maintenance Data Steward",
                  "sensitivity": "CUI"},
        "supply": {"entities": ["inventory", "product", "purchase_order", "supplier", "sales_order", "shipment"],
                   "owner": "Supply Chain Manager", "steward": "Supply Data Steward",
                   "sensitivity": "Internal"},
    },
    # required fields; a tuple means "any one of"
    "required": {
        "inventory": ["sku", "quantity", "location"],
        "product": ["sku", "description"],
        "purchase_order": ["order_id", "supplier", "sku", "quantity", "promised_date"],
        "sales_order": ["order_id", "sku", "quantity"],
        "shipment": ["shipment_id", "status", "promised_date"],
        "supplier": ["supplier"],
        "sensor_reading": [["asset_id", "device_id"], "value", "timestamp"],
        "asset_status": ["asset_id", "status", "mc_rate"],
        "subsystem_health": ["asset_id", "subsystem", "health_score"],
        "fault": ["asset_id", "fault_code", "severity", "date_logged", "description"],
        "production_schedule": ["asset_id", "induction_date", "due_date"],
        "gate_event": ["asset_id", "gate", "goal_days"],
        "asset_flow": ["asset_id", "start_date", "amrep_date", "as_of"],
        "gate_plan": ["gate", "wip_plan", "required_days"],
    },
    # natural keys; "snapshot" keeps the newest version, "event" removes duplicate copies
    "keys": {
        "inventory": ("snapshot", ["sku", "location"]),
        "product": ("snapshot", ["sku"]),
        "supplier": ("snapshot", ["supplier"]),
        "asset_status": ("snapshot", ["asset_id"]),
        "asset_flow": ("snapshot", ["asset_id"]),
        "subsystem_health": ("snapshot", ["asset_id", "subsystem"]),
        "production_schedule": ("snapshot", ["asset_id"]),
        "gate_plan": ("snapshot", ["gate"]),
        "purchase_order": ("event", ["order_id", "sku"]),
        "sales_order": ("event", ["order_id", "sku"]),
        "shipment": ("event", ["shipment_id"]),
        "gate_event": ("event", ["asset_id", "gate"]),
        "fault": ("event", ["asset_id", "fault_code", "date_logged"]),
        "sensor_reading": ("event", ["asset_id", "device_id", "metric", "timestamp"]),
    },
    # identity fields whose absence is a hard rule (record cannot be placed or matched)
    "hard_keys": {
        "inventory": ["sku"], "product": ["sku"], "supplier": ["supplier"], "purchase_order": ["order_id"],
        "sales_order": ["order_id"], "shipment": ["shipment_id"], "asset_status": ["asset_id"],
        "asset_flow": ["asset_id"], "subsystem_health": ["asset_id", "subsystem"], "fault": ["asset_id"],
        "production_schedule": ["asset_id"], "gate_event": ["asset_id", "gate"], "gate_plan": ["gate"],
        "sensor_reading": [["asset_id", "device_id"]],
    },
    # field used for timeliness and how old it may be relative to the newest record of that type
    "freshness": {
        "sensor_reading": ["timestamp", 1], "fault": ["date_logged", 90], "asset_flow": ["as_of", 14],
        "purchase_order": ["order_date", 180], "shipment": ["promised_date", 60],
        "production_schedule": ["induction_date", 730],
    },
    "enums": {
        "severity": ["Critical", "Major", "Minor", "High", "Medium", "Low", "Warning"],
        "gate": ["Queue to Pre Dock", "Pre Dock", "Queue to Inspection", "Inspection", "Queue to Repair",
                 "Repair", "Queue to Restore", "Restore", "Queue to Post Dock", "Post Dock"],
    },
    "products": [
        {"id": "depot_flow", "name": "Depot Flow & Gate Performance", "domain": "depot",
         "description": "Aircraft flow through the PDM gates, WIP plan and gate history.",
         "entities": ["asset_flow", "gate_event", "gate_plan"], "freshness_days": 7},
        {"id": "production_schedule", "name": "Production Schedule", "domain": "depot",
         "description": "Induction, due (AMREP) and sell dates by tail.",
         "entities": ["production_schedule"], "freshness_days": 30},
        {"id": "fleet_health", "name": "Fleet Health & Reliability", "domain": "fleet",
         "description": "Mission-capable status, subsystem health, telemetry and faults.",
         "entities": ["asset_status", "subsystem_health", "sensor_reading", "fault"], "freshness_days": 1},
        {"id": "inventory_position", "name": "Inventory Position", "domain": "supply",
         "description": "On-hand stock, reorder points and item master.",
         "entities": ["inventory", "product"], "freshness_days": 7},
        {"id": "supplier_performance", "name": "Supplier Performance", "domain": "supply",
         "description": "Purchase-order lines, promised vs received, supplier master.",
         "entities": ["purchase_order", "supplier"], "freshness_days": 7},
        {"id": "logistics", "name": "Logistics & Shipments", "domain": "supply",
         "description": "Inbound and outbound shipments against ETA.",
         "entities": ["shipment", "sales_order"], "freshness_days": 3},
    ],
}

DIMENSIONS = list(DEFAULT_CONFIG["weights"])
GRADE_ACTION = {"A": "Trusted: shown normally; feeds Gold and AI",
                "B": "Usable with caution: amber flag; Gold and AI with a stated caveat",
                "C": "Limited: visible in Silver only; excluded from Gold and AI",
                "D": "Untrusted: quarantined; steward fixes at source"}
GOLD_GRADES = ("A", "B")


def config() -> dict:
    doc = get_db().governance_config.find_one({"_id": "active"})
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    if doc:
        for k, v in doc.get("config", {}).items():
            cfg[k] = v
    return cfg


def update_config(changes: dict, user: str = "system") -> dict:
    allowed = {"weights", "grades", "confidence", "quarantine_sla_days", "drift_threshold_pct",
               "gold_target_pct", "domains", "asset_id_pattern"}
    bad = set(changes) - allowed
    if bad:
        raise ValueError(f"Not editable: {', '.join(sorted(bad))}")
    if "weights" in changes and round(sum(changes["weights"].values())) != 100:
        raise ValueError("Weights must add up to 100")
    cfg = config()
    cfg.update(changes)
    cfg["version"] = cfg.get("version", 1) + 1
    get_db().governance_config.update_one(
        {"_id": "active"}, {"$set": {"config": {k: cfg[k] for k in allowed | {"version"}}}}, upsert=True)
    audit("config_change", user, {"changed": sorted(changes), "version": cfg["version"]})
    run()
    return cfg


def grade_for(score: Optional[float], cfg: Optional[dict] = None) -> Optional[str]:
    if score is None:
        return None
    g = (cfg or config())["grades"]
    return "A" if score >= g["A"] else "B" if score >= g["B"] else "C" if score >= g["C"] else "D"


def domain_of(record_type: str, cfg: dict) -> str:
    for name, d in cfg["domains"].items():
        if record_type in d["entities"]:
            return name
    return "other"


def audit(action: str, user: str, detail: dict) -> None:
    get_db().audit.insert_one({"at": now(), "action": action, "user": user, "detail": detail})


def _naive(d):
    if isinstance(d, datetime):
        return d.replace(tzinfo=None) if d.tzinfo else d
    return d


def _present(v) -> bool:
    return v is not None and v != ""


# ---------------------------------------------------------------- record scoring

def _master_assets() -> set:
    db = get_db()
    ids = set()
    for rt in ("asset_status", "asset_flow", "production_schedule"):
        ids.update(x for x in db.records.distinct("asset_id", {"record_type": rt}) if x)
    return ids


def _score_record(r: dict, cfg: dict, ctx: dict) -> dict:
    rt = r["record_type"]
    issues: list[dict] = []           # {dimension, code, detail, hard}
    dims: dict[str, float] = {}

    def issue(dim, code, detail, hard=False):
        issues.append({"dimension": dim, "code": code, "detail": detail, "hard": hard})

    # completeness
    req = cfg["required"].get(rt, [])
    if req:
        have = 0
        for f in req:
            ok = any(_present(r.get(x)) for x in f) if isinstance(f, list) else _present(r.get(f))
            have += ok
            if not ok:
                issue("completeness", "MISSING_FIELD", f"{'/'.join(f) if isinstance(f, list) else f} is empty")
        dims["completeness"] = 100.0 * have / len(req)
    else:
        dims["completeness"] = 100.0

    # validity
    checks, failed = 0, 0
    for c in r.get("cleaning", []):
        if c["issue"] in ("not a number", "not a date"):
            checks += 1
            failed += 1
            issue("validity", "UNPARSEABLE", f"{c['field']}: '{c['raw']}' is {c['issue']}")
    for f, allowed in cfg["enums"].items():
        if f in ENTITIES.get(rt, {}) and _present(r.get(f)):
            checks += 1
            if r[f] not in allowed:
                failed += 1
                issue("validity", "INVALID_CODE", f"{f} '{r[f]}' is not in the approved list", hard=True)
    if "asset_id" in ENTITIES.get(rt, {}) and _present(r.get("asset_id")) and domain_of(rt, cfg) in ("depot", "fleet"):
        checks += 1
        if not re.match(cfg["asset_id_pattern"], str(r["asset_id"])):
            failed += 1
            issue("validity", "INVALID_ID", f"asset id '{r['asset_id']}' does not match the tail-number format", hard=True)
    for f, lo, hi in (("health_score", 0, 100), ("mc_rate", 0, 100), ("quantity", 0, None),
                      ("unit_cost", 0, None), ("goal_days", 0, None), ("actual_days", 0, None)):
        v = r.get(f)
        if isinstance(v, (int, float)) and f in ENTITIES.get(rt, {}):
            checks += 1
            if v < lo or (hi is not None and v > hi):
                failed += 1
                issue("validity", "OUT_OF_RANGE", f"{f} = {v} is outside {lo}–{hi if hi is not None else '∞'}",
                      hard=f in ("quantity", "health_score", "mc_rate"))
    for f in ENTITIES.get(rt, {}):
        v = _naive(r.get(f))
        if isinstance(v, datetime):
            checks += 1
            if not (1990 <= v.year <= 2100):
                failed += 1
                issue("validity", "IMPLAUSIBLE_DATE", f"{f} = {v:%Y-%m-%d}")
    dims["validity"] = 100.0 if checks == 0 else 100.0 * (checks - failed) / checks

    # timeliness (relative to the newest record of the same type; data may be dated ahead)
    fr = cfg["freshness"].get(rt)
    if fr and _naive(r.get(fr[0])) and ctx["latest"].get(rt):
        age = (ctx["latest"][rt] - _naive(r[fr[0]])).days
        limit = fr[1]
        dims["timeliness"] = 100.0 if age <= limit else 70.0 if age <= 2 * limit else 40.0
        if age > limit:
            issue("timeliness", "STALE", f"{fr[0]} is {age} days older than the newest {rt} (limit {limit})")
    else:
        dims["timeliness"] = 100.0

    # consistency
    cons = []
    if "asset_id" in ENTITIES.get(rt, {}) and _present(r.get("asset_id")) and domain_of(rt, cfg) in ("depot", "fleet"):
        ok = r["asset_id"] in ctx["master"]
        cons.append(ok)
        if not ok:
            issue("consistency", "NOT_IN_MASTER", f"{r['asset_id']} is not in the fleet master")
    if rt == "gate_event":
        s, f = _naive(r.get("start_date")), _naive(r.get("actual_finish"))
        if s and f:
            ok = f >= s
            cons.append(ok)
            if not ok:
                issue("consistency", "DATES_REVERSED", f"finish {f:%Y-%m-%d} before start {s:%Y-%m-%d}")
            elif r.get("actual_days") is not None:
                ok = abs((f - s).days - r["actual_days"]) <= 2
                cons.append(ok)
                if not ok:
                    issue("consistency", "DAYS_MISMATCH",
                          f"actual days {r['actual_days']:.0f} vs {(f - s).days} between dates")
    if rt == "asset_flow":
        dq = (r.get("attributes") or {}).get("data_quality")
        cons.append(not dq)
        if dq:
            issue("consistency", "ID_MISMATCH", dq)
    if rt == "asset_status" and r.get("mc_rate") is not None and r.get("status"):
        mc = r["mc_rate"] * 100 if r["mc_rate"] <= 1 else r["mc_rate"]
        st = str(r["status"]).lower()
        ok = not ((st in ("degraded", "down") and mc >= 95) or (st == "up" and mc < 60))
        cons.append(ok)
        if not ok:
            issue("consistency", "STATUS_CONFLICT", f"status '{r['status']}' but MC rate {mc:.1f}%")
    if rt == "purchase_order":
        o, d = _naive(r.get("order_date")), _naive(r.get("delivered_date"))
        if o and d:
            cons.append(d >= o)
            if d < o:
                issue("consistency", "DATES_REVERSED", "received before ordered")
        q, u, t = r.get("quantity"), r.get("unit_cost"), r.get("total_value")
        if q and u and t:
            ok = abs(q * u - t) <= 0.05 * abs(t)
            cons.append(ok)
            if not ok:
                issue("consistency", "TOTAL_MISMATCH", f"{q} × {u} ≠ {t}")
    if rt == "sensor_reading" and r.get("threshold_low") is not None and r.get("threshold_high") is not None:
        ok = r["threshold_low"] < r["threshold_high"]
        cons.append(ok)
        if not ok:
            issue("consistency", "LIMITS_REVERSED", "low limit is above high limit")
    dims["consistency"] = 100.0 if not cons else 100.0 * sum(cons) / len(cons)

    # uniqueness (filled in by the dedup pass)
    dims["uniqueness"] = 0.0 if r.get("_dup_of") else 100.0

    # provenance
    b = ctx["batches"].get(r.get("batch_id"))
    if not b:
        dims["provenance"] = 40.0
        issue("provenance", "NO_LINEAGE", "no Bronze batch for this record")
    else:
        p = 100.0
        if r.get("source_type") in ("document", "pdf") or (r.get("attributes") or {}).get("document_kind"):
            p = 80.0   # read by AI from an unstructured document: lineage kept, values interpreted
        dims["provenance"] = p

    # key fields must exist
    for k in cfg["hard_keys"].get(rt, []):
        alts = k if isinstance(k, list) else [k]
        if not any(_present(r.get(x)) for x in alts):
            issue("completeness", "MISSING_KEY", f"key field {'/'.join(alts)} is empty", hard=True)

    w = cfg["weights"]
    score = round(sum(dims[d] * w[d] for d in DIMENSIONS) / sum(w.values()), 1)
    hard = any(i["hard"] for i in issues)
    grade = "D" if hard else grade_for(score, cfg)
    return {"score": score, "grade": grade, "dims": {k: round(v, 1) for k, v in dims.items()},
            "issues": issues, "hard_fail": hard}


# ---------------------------------------------------------------- dedup / snapshots

def _key(r: dict, fields: list[str]) -> tuple:
    out = []
    for f in fields:
        v = _naive(r.get(f))
        out.append(v.isoformat() if isinstance(v, datetime) else (str(v).strip().lower() if v is not None else None))
    return tuple(out)


def _content(r: dict, rt: str) -> tuple:
    return tuple((f, str(_naive(r.get(f)))) for f in sorted(ENTITIES.get(rt, {})))


def _resolve_versions(recs: list[dict], cfg: dict) -> None:
    """Mark superseded snapshots and duplicate event copies (no deletes: lineage stays)."""
    by_type = defaultdict(list)
    for r in recs:
        by_type[r["record_type"]].append(r)
    for rt, rs in by_type.items():
        kind, fields = cfg["keys"].get(rt, (None, None))
        if not kind:
            continue
        groups = defaultdict(list)
        for r in rs:
            k = _key(r, fields)
            if all(x is None for x in k):
                continue
            groups[k].append(r)
        for group in groups.values():
            if len(group) < 2:
                continue
            group.sort(key=lambda r: (_naive(r.get("ingested_at")) or datetime.min, str(r["_id"])))
            if kind == "snapshot":
                keep = group[-1]
            else:
                # identical copies: the first one received stands; if the source re-sent a
                # different version (a correction), the newest version stands
                sigs = {_content(r, rt) for r in group}
                keep = group[0] if len(sigs) == 1 else group[-1]
            for r in group:
                if r is keep:
                    continue
                if kind == "snapshot":
                    r["_superseded_by"] = keep["_id"]
                else:
                    r["_dup_of"] = keep["_id"]


# ---------------------------------------------------------------- the governance run

def run(record_types: Optional[set] = None) -> dict:
    """Score records, resolve versions, route quarantine, refresh product status."""
    db = get_db()
    cfg = config()
    q = {"record_type": {"$in": sorted(record_types)}} if record_types else {}
    recs = list(db.records.find(q, {"attributes.board": 0}))
    if not recs:
        return {"scored": 0}
    latest = {}
    for rt, (field, _) in cfg["freshness"].items():
        dates = [_naive(r.get(field)) for r in recs if r["record_type"] == rt and isinstance(r.get(field), datetime)]
        if dates:
            latest[rt] = max(dates)
    ctx = {"latest": latest, "master": _master_assets(),
           "batches": {b["batch_id"]: b for b in db.raw.find({}, {"payload": 0})}}
    _resolve_versions(recs, cfg)

    counts = Counter()
    open_q = {x["record_id"]: x for x in db.quarantine.find({"status": "open"})}
    for r in recs:
        t = _score_record(r, cfg, ctx)
        if r.get("_superseded_by"):
            layer = "history"
        elif r.get("_dup_of"):
            layer = "duplicate"
        elif t["grade"] == "D":
            layer = "quarantine"
        else:
            layer = "silver"
        dom = domain_of(r["record_type"], cfg)
        tags = {"domain": dom, "sensitivity": cfg["domains"].get(dom, {}).get("sensitivity", "Internal"),
                "master_matched": not any(i["code"] == "NOT_IN_MASTER" for i in t["issues"])}
        db.records.update_one({"_id": r["_id"]}, {"$set": {"trust": t, "layer": layer, "tags": tags,
                                                           "superseded_by": r.get("_superseded_by"),
                                                           "duplicate_of": r.get("_dup_of")}})
        counts[layer] += 1
        counts[f"grade_{t['grade']}"] += 1
        if layer == "quarantine" and r["_id"] not in open_q:
            reasons = [i for i in t["issues"] if i["hard"]] or [i for i in t["issues"]][:1]
            db.quarantine.insert_one({
                "record_id": r["_id"], "record_type": r["record_type"], "asset_id": r.get("asset_id"),
                "key": " · ".join(str(r.get(f)) for f in cfg["keys"].get(r["record_type"], (None, []))[1] if r.get(f) is not None),
                "reason_code": reasons[0]["code"] if reasons else "LOW_SCORE",
                "reason": reasons[0]["detail"] if reasons else f"score {t['score']}",
                "score": t["score"], "domain": dom,
                "steward": cfg["domains"].get(dom, {}).get("steward", "Data Steward"),
                "source_id": r.get("source_id"), "batch_id": r.get("batch_id"),
                "opened_at": now(), "due_at": now() + timedelta(days=cfg["quarantine_sla_days"]),
                "status": "open"})
        elif layer != "quarantine" and r["_id"] in open_q:
            why = ("replaced by a corrected record from the source" if layer in ("history", "duplicate")
                   else "record no longer fails the rules")
            db.quarantine.update_one({"_id": open_q[r["_id"]]["_id"]},
                                     {"$set": {"status": "resolved", "resolved_at": now(), "resolution": why}})
    # a quarantined record superseded by a newer good version is resolved at the source
    for qd in db.quarantine.find({"status": "open"}):
        cur = db.records.find_one({"_id": qd["record_id"]}, {"layer": 1})
        if cur is None or cur.get("layer") in ("history", "duplicate"):
            db.quarantine.update_one({"_id": qd["_id"]}, {"$set": {
                "status": "resolved", "resolved_at": now(),
                "resolution": "replaced by a corrected record from the source"}})
    _detect_drift(cfg)
    audit("governance_run", "system", {"record_types": sorted(record_types or []), **counts})
    return {"scored": len(recs), **counts}


def gold_query(extra: Optional[dict] = None) -> dict:
    """Mongo filter for Gold-eligible records (what Maestro modules and the AI read)."""
    return {"layer": "silver", "trust.grade": {"$in": list(GOLD_GRADES)}, **(extra or {})}


def governed() -> bool:
    return get_db().records.find_one({"trust": {"$exists": True}}, {"_id": 1}) is not None


# ---------------------------------------------------------------- drift

def _detect_drift(cfg: dict) -> None:
    """Flag numeric fields whose newest batch mean moved more than the threshold vs earlier batches."""
    db = get_db()
    thr = cfg["drift_threshold_pct"] / 100
    alerts = []
    # compare loads of the *same dataset* (entity + sheet), e.g. weekly snapshots of one tracker,
    # never two different sheets that happen to share an entity
    batches_by_type = defaultdict(list)
    for b in db.raw.find({"records_stored": {"$gt": 0}}, {"payload": 0}).sort("received_at", 1):
        m = b.get("meta") or {}
        rt = m.get("record_type")
        if rt in ENTITIES:
            batches_by_type[(rt, m.get("sheet") or "")].append(b["batch_id"])
    for (rt, _sheet), batch_ids in batches_by_type.items():
        if len(batch_ids) < 2:
            continue
        last, prev = batch_ids[-1], batch_ids[:-1]
        for f in [f for f in ENTITIES[rt] if f in NUMERIC_FIELDS]:
            cur = [r[f] for r in db.records.find({"batch_id": last, f: {"$ne": None}}, {f: 1})]
            old = [r[f] for r in db.records.find({"batch_id": {"$in": prev}, f: {"$ne": None}}, {f: 1})]
            if len(cur) < 5 or len(old) < 5:
                continue
            m0, m1 = mean(old), mean(cur)
            base = abs(m0) or pstdev(old) or 1.0
            change = (m1 - m0) / base
            if abs(change) > thr:
                alerts.append({"record_type": rt, "field": f, "previous_mean": round(m0, 2),
                               "current_mean": round(m1, 2), "change_pct": round(change * 100, 1),
                               "batch_id": last, "detected_at": now()})
    db.drift.delete_many({})
    if alerts:
        db.drift.insert_many(alerts)


# ---------------------------------------------------------------- Gold data products

def products() -> list[dict]:
    db = get_db()
    cfg = config()
    certs = {c["_id"]: c for c in db.certifications.find({})}
    out = []
    for p in cfg["products"]:
        ents = p["entities"]
        live = list(db.records.find({"record_type": {"$in": ents}, "layer": {"$in": ["silver", "quarantine"]}},
                                    {"trust.score": 1, "trust.grade": 1, "layer": 1, "ingested_at": 1}))
        if not live:
            continue
        gold = [r for r in live if r["layer"] == "silver" and r["trust"]["grade"] in GOLD_GRADES]
        excluded_c = sum(1 for r in live if r["layer"] == "silver" and r["trust"]["grade"] == "C")
        quarantined = sum(1 for r in live if r["layer"] == "quarantine")
        score = round(mean(r["trust"]["score"] for r in gold), 1) if gold else None
        grade = grade_for(score, cfg)
        last = max(_naive(r["ingested_at"]) for r in live)
        fresh = (_naive(now()) - last) <= timedelta(days=p["freshness_days"])
        cert = certs.get(p["id"])
        if grade not in GOLD_GRADES:
            status = "BLOCKED"
        elif cert and _naive(cert["certified_at"]) >= last:
            status = "CERTIFIED"
        elif cert:
            status = "RECERTIFY"
        else:
            status = "PENDING"
        flags = []
        if grade == "B":
            flags.append("amber: usable with caution")
        if quarantined:
            flags.append(f"{quarantined} in quarantine")
        if excluded_c:
            flags.append(f"{excluded_c} grade C excluded")
        out.append({**{k: p[k] for k in ("id", "name", "description", "domain", "entities", "freshness_days")},
                    "owner": cfg["domains"].get(p["domain"], {}).get("owner"),
                    "score": score, "grade": grade, "records": len(gold), "quarantined": quarantined,
                    "excluded_c": excluded_c, "freshness": "CURRENT" if fresh else "STALE",
                    "last_loaded": last, "certification": status,
                    "certified_by": cert.get("certified_by") if cert else None,
                    "certified_at": cert.get("certified_at") if cert else None,
                    "trust_label": f"{grade} ({score})" + (" · " + " · ".join(flags) if flags else "") if grade else "no Gold data"})
    return out


def certify(product_id: str, user: str, note: str = "") -> dict:
    p = next((x for x in products() if x["id"] == product_id), None)
    if not p:
        raise KeyError(product_id)
    if p["grade"] not in GOLD_GRADES:
        raise ValueError(f"Gate 2: grade {p['grade']} is below B; fix the data before certifying")
    get_db().certifications.update_one({"_id": product_id}, {"$set": {
        "certified_by": user, "certified_at": now(), "score": p["score"], "grade": p["grade"],
        "definition_version": config()["version"], "note": note}}, upsert=True)
    audit("certify", user, {"product": product_id, "score": p["score"], "grade": p["grade"], "note": note})
    return next(x for x in products() if x["id"] == product_id)


def trust_label_for(entities: list[str]) -> Optional[dict]:
    for p in products():
        if set(entities) & set(p["entities"]):
            return {"product": p["name"], "grade": p["grade"], "score": p["score"],
                    "certification": p["certification"], "label": p["trust_label"]}
    return None


# ---------------------------------------------------------------- quarantine

def quarantine(status: str = "open") -> list[dict]:
    items = []
    for qd in get_db().quarantine.find({"status": status} if status != "all" else {}).sort("opened_at", -1):
        qd["id"] = str(qd.pop("_id"))
        qd["record_id"] = str(qd["record_id"])
        qd["age_days"] = (_naive(now()) - _naive(qd["opened_at"])).days
        qd["overdue"] = qd["status"] == "open" and _naive(now()) > _naive(qd["due_at"])
        items.append(qd)
    return items


def close_quarantine(qid: str, user: str, resolution: str) -> dict:
    from bson import ObjectId
    if not resolution.strip():
        raise ValueError("A resolution note is required")
    db = get_db()
    oid = ObjectId(qid)
    qd = db.quarantine.find_one({"_id": oid})
    if not qd:
        raise KeyError(qid)
    db.quarantine.update_one({"_id": oid}, {"$set": {"status": "closed", "resolved_at": now(),
                                                     "resolution": resolution, "closed_by": user}})
    audit("quarantine_close", user, {"record_id": str(qd["record_id"]), "resolution": resolution,
                                     "note": "record stays quarantined; fix arrives as a new record from the source"})
    return {"status": "closed"}


# ---------------------------------------------------------------- AI decision gate

def decision_gate(grade: Optional[str], confidence: Optional[float], cfg: Optional[dict] = None) -> dict:
    cfg = cfg or config()
    c = cfg["confidence"]
    band = None if confidence is None else "high" if confidence >= c["high"] else "medium" if confidence >= c["medium"] else "low"
    matrix = {"A": {"high": "RECOMMEND", "medium": "RECOMMEND_WITH_CAVEAT", "low": "ADVISORY"},
              "B": {"high": "RECOMMEND_WITH_CAVEAT", "medium": "ADVISORY", "low": "WITHHOLD"}}
    if grade not in GOLD_GRADES:
        verdict, why = "WITHHOLD", "data insufficient (grade below B); routed to data steward"
    elif band is None:
        verdict, why = "WITHHOLD", "no model confidence available"
    else:
        verdict = matrix[grade][band]
        why = {"RECOMMEND": "data and model both clear the bar",
               "RECOMMEND_WITH_CAVEAT": "shown with the data or model limitation stated",
               "ADVISORY": "insight only; a person must analyse before acting",
               "WITHHOLD": "confidence too low for this data grade"}[verdict]
    return {"verdict": verdict, "confidence_band": band, "reason": why}


def record_decision(question: str, answer: dict, data: dict, model: str) -> dict:
    gate = decision_gate(data.get("grade"), answer.get("confidence"))
    doc = {"created_at": now(), "question": question, "answer": answer, "data_trust": data,
           "model": model, "gate": gate, "status": "PENDING", "history": []}
    if gate["verdict"] == "WITHHOLD":
        doc["status"] = "WITHHELD"
    res = get_db().decisions.insert_one(doc)
    doc["id"] = str(res.inserted_id)
    doc.pop("_id", None)
    return doc


def act_on_decision(did: str, action: str, user: str, text: str = "") -> dict:
    from bson import ObjectId
    db = get_db()
    d = db.decisions.find_one({"_id": ObjectId(did)})
    if not d:
        raise KeyError(did)
    if d["status"] not in ("PENDING",):
        raise ValueError(f"Decision is already {d['status']}")
    if action == "override" and not text.strip():
        raise ValueError("An override needs a reason")
    if action == "modify" and not text.strip():
        raise ValueError("Give the modified action")
    status = {"accept": "ACCEPTED", "modify": "MODIFIED", "override": "OVERRIDDEN"}[action]
    db.decisions.update_one({"_id": d["_id"]}, {"$set": {"status": status, "decided_by": user, "decided_at": now(),
                                                         "decision_note": text},
                                                "$push": {"history": {"at": now(), "action": action, "user": user, "text": text}}})
    audit(f"decision_{action}", user, {"decision": did, "text": text})
    return {"id": did, "status": status}


def decisions(limit: int = 50) -> list[dict]:
    out = []
    for d in get_db().decisions.find({}).sort("created_at", -1).limit(limit):
        d["id"] = str(d.pop("_id"))
        out.append(d)
    return out


def overall_trust() -> dict:
    ps = [p for p in products() if p["score"] is not None]
    if not ps:
        return {"score": None, "grade": None}
    total = sum(p["records"] for p in ps) or 1
    score = round(sum(p["score"] * p["records"] for p in ps) / total, 1)
    return {"score": score, "grade": grade_for(score)}


# ---------------------------------------------------------------- views for the UI

def overview() -> dict:
    db = get_db()
    raw = list(db.raw.find({}, {"payload": 0}))
    bronze_rows = sum(b.get("rows_received", 0) for b in raw)
    blocked = sum(b.get("rows_received", 0) for b in raw if (b.get("gate1") or {}).get("status") == "blocked")
    layer_counts = Counter(r.get("layer") for r in db.records.find({}, {"layer": 1}))
    silver = list(db.records.find({"layer": "silver"}, {"trust.score": 1, "trust.grade": 1, "record_type": 1}))
    ps = products()
    gold_records = sum(p["records"] for p in ps)
    silver_types = {r["record_type"] for r in silver}
    pending = [d for d in decisions(200) if d["status"] == "PENDING"]
    all_dec = decisions(1000)
    decided = [d for d in all_dec if d["status"] in ("ACCEPTED", "MODIFIED", "OVERRIDDEN")]
    open_q = quarantine("open")
    drift = list(db.drift.find({}, {"_id": 0}))
    grade_mix = Counter(r["trust"]["grade"] for r in silver)
    grade_mix["D"] += layer_counts.get("quarantine", 0)
    ab = grade_mix["A"] + grade_mix["B"]
    scored = sum(grade_mix.values())
    trust = overall_trust()
    return {
        "kpis": {
            "datasets_governed": len(raw), "bronze_datasets": len({b["source_id"] for b in raw}),
            "silver_datasets": len(silver_types),
            "gold_confidence": trust["score"], "gold_products": len(ps),
            "blocked_at_ingest": blocked, "bronze_records": bronze_rows,
            "open_alerts": sum(1 for q in open_q) + len(drift),
            "review_queue": len(open_q) + len(pending), "quarantine": len(open_q), "pending_decisions": len(pending),
            "drift_alerts": len(drift),
        },
        "layers": {
            "bronze": {"datasets": len(raw), "records": bronze_rows,
                       "files": db.bronze_files.count_documents({})},
            "silver": {"datasets": len(silver_types), "records": len(silver),
                       "avg_trust": round(mean(r["trust"]["score"] for r in silver), 1) if silver else None,
                       "grade_mix": dict(grade_mix),
                       "history": layer_counts.get("history", 0), "duplicates": layer_counts.get("duplicate", 0),
                       "quarantine": layer_counts.get("quarantine", 0)},
            "gold": {"datasets": len(ps), "records": gold_records, "avg_trust": trust["score"],
                     "certified": sum(p["certification"] == "CERTIFIED" for p in ps)},
            "decisions": {"total": len(all_dec), "pending": len(pending), "decided": len(decided)},
        },
        "trust": trust,
        "monitor": {
            "gold_ab_pct": round(100 * ab / scored, 1) if scored else None,
            "gold_target_pct": config()["gold_target_pct"],
            "quarantine_avg_age_days": round(mean(q["age_days"] for q in open_q), 1) if open_q else 0,
            "quarantine_overdue": sum(q["overdue"] for q in open_q),
            "acceptance_rate": round(100 * sum(d["status"] == "ACCEPTED" for d in decided) / len(decided), 1) if decided else None,
            "override_rate": round(100 * sum(d["status"] == "OVERRIDDEN" for d in decided) / len(decided), 1) if decided else None,
        },
        "products": ps,
        "latest_decision": pending[0] if pending else (all_dec[0] if all_dec else None),
    }


def stage(name: str) -> dict:
    db = get_db()
    cfg = config()
    if name == "plan":
        return {"config": {k: cfg[k] for k in ("version", "weights", "grades", "confidence", "quarantine_sla_days",
                                                "drift_threshold_pct", "gold_target_pct", "domains", "required",
                                                "freshness", "enums", "asset_id_pattern")},
                "grade_actions": GRADE_ACTION,
                "decision_matrix": {g: {b: decision_gate(g, v, cfg)["verdict"] for b, v in
                                        (("high", 0.9), ("medium", 0.7), ("low", 0.3))} for g in "ABCD"},
                "products": cfg["products"]}
    if name == "filter":
        batches = []
        for b in db.raw.find({}, {"payload": 0}).sort("received_at", -1).limit(200):
            m = b.get("meta") or {}
            batches.append({"batch_id": b["batch_id"], "source": b["source_id"], "sheet": m.get("sheet"),
                            "record_type": m.get("record_type"), "received_at": b["received_at"],
                            "rows": b.get("rows_received"), "stored": b.get("records_stored"),
                            "sha256": (b.get("payload_sha256") or "")[:16], "gate1": b.get("gate1")})
        dups = list(db.records.find({"layer": "duplicate"}, {"_id": 0, "record_type": 1, "asset_id": 1, "order_id": 1,
                                                              "sku": 1, "source_id": 1, "ingested_at": 1}).limit(200))
        hist = Counter(r["record_type"] for r in db.records.find({"layer": "history"}, {"record_type": 1}))
        files = [{k: f.get(k) for k in ("filename", "sha256", "size", "received_at")}
                 for f in db.bronze_files.find({}, {"content": 0}).sort("received_at", -1).limit(100)]
        return {"files": files, "batches": batches, "duplicates": dups, "superseded": dict(hist),
                "gate1_checks": ["file received and hashed", "parsed by a known reader",
                                 "schema recognised (maps to a Maestro entity)",
                                 "malware scan: not configured in this deployment"]}
    if name == "clean":
        counts = Counter()
        examples = []
        for r in db.records.find({"cleaning": {"$exists": True}}, {"cleaning": 1, "record_type": 1}):
            for c in r["cleaning"]:
                counts[(r["record_type"], c["field"], c["issue"])] += 1
                if len(examples) < 50 and c["issue"] != "date standardised":
                    examples.append({"record_type": r["record_type"], **c})
        return {"transforms": ["column names mapped to canonical fields (AI or rules)",
                               "numbers parsed (currency, thousands separators, units like 'hrs')",
                               "dates standardised to ISO 8601", "Excel blank dates (1899-12-29) treated as empty",
                               "text trimmed"],
                "summary": [{"record_type": k[0], "field": k[1], "issue": k[2], "count": v} for k, v in counts.most_common()],
                "examples": examples}
    if name == "enrich":
        by_type = defaultdict(lambda: {"records": 0, "matched": 0, "with_asset": 0})
        for r in db.records.find({"layer": {"$in": ["silver", "quarantine"]}}, {"record_type": 1, "asset_id": 1, "tags": 1}):
            s = by_type[r["record_type"]]
            s["records"] += 1
            if r.get("asset_id"):
                s["with_asset"] += 1
                s["matched"] += bool((r.get("tags") or {}).get("master_matched"))
        rows = []
        for rt, s in sorted(by_type.items()):
            dom = domain_of(rt, cfg)
            rows.append({"record_type": rt, "domain": dom,
                         "sensitivity": cfg["domains"].get(dom, {}).get("sensitivity", "Internal"),
                         "owner": cfg["domains"].get(dom, {}).get("owner"), "records": s["records"],
                         "master_match_pct": round(100 * s["matched"] / s["with_asset"], 1) if s["with_asset"] else None})
        return {"master_assets": len(_master_assets()), "entities": rows}
    if name == "audit":
        balance = []
        for b in db.raw.find({}, {"payload": 0}).sort("received_at", -1).limit(200):
            ls = Counter(r.get("layer") for r in db.records.find({"batch_id": b["batch_id"]}, {"layer": 1}))
            rows = b.get("rows_received", 0)
            stored = b.get("records_stored", 0)
            blocked = rows if (b.get("gate1") or {}).get("status") == "blocked" else 0
            replaced = max(stored - sum(ls.values()), 0)
            balance.append({"batch_id": b["batch_id"][:10], "source": b["source_id"],
                            "sheet": (b.get("meta") or {}).get("sheet"), "received": rows, "blocked": blocked,
                            "silver": ls.get("silver", 0), "quarantine": ls.get("quarantine", 0),
                            "duplicate": ls.get("duplicate", 0), "history": ls.get("history", 0),
                            "replaced": replaced,
                            "balanced": rows == blocked + sum(ls.values()) + replaced or b.get("rows_received") is None})
        log = [{**{k: a.get(k) for k in ("at", "action", "user")}, "detail": a.get("detail")}
               for a in db.audit.find({}).sort("at", -1).limit(100)]
        return {"balance": balance, "audit_log": log}
    if name == "monitor":
        ov = overview()
        return {"monitor": ov["monitor"], "drift": list(db.drift.find({}, {"_id": 0})),
                "quarantine": quarantine("open"), "decisions": decisions(50)}
    raise KeyError(name)
