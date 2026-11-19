"""Maestro-SC depot analytics (Programmed Depot Maintenance flow).

Implements the "Fixer data to graphs" process:

* Gate graph - for one gate (queues count as gates): one bar per aircraft for
  the last N aircraft that completed the gate plus the aircraft in it now
  (actual + remaining days), the rolling last-N average and the requirement,
  and a WIP inset: plan (machine info) vs actual (started, not finished).
* Machine - last-N average days per gate vs requirement, and total.
* Dock overview - every aircraft in work: current gate, day x/goal, flow
  x/AMREP days, in-dock x/goal, queue days, AMREP risk.
* Fleet health and alerts - mission-capable bands, subsystem health,
  telemetry threshold breaches, fault registry.
"""
from collections import defaultdict
from datetime import datetime, timezone
from statistics import mean
from typing import Optional

from .connectors.sheets import GATES
from .db import get_db

LAST_N = 6
GATE_ORDER = [name for _, name, _, _ in GATES]
GATE_NUMBER = {name: num for _, name, num, _ in GATES}
IS_QUEUE = {name: q for _, name, _, q in GATES}
IN_DOCK_GATES = {"Inspection", "Queue to Repair", "Repair", "Queue to Restore", "Restore"}

# Default health bands (from the dashboard supporting data); used for MC rate.
HEALTH_BANDS = [(95, "FMC", "Full Mission Capable", "good"),
                (80, "PMC", "Partial Mission Capable", "warning"),
                (60, "DEG", "Degraded", "serious"),
                (0, "NMC", "Non-Mission Capable", "critical")]


def _naive(d: Optional[datetime]) -> Optional[datetime]:
    if d is None:
        return None
    return d.replace(tzinfo=None) if d.tzinfo else d


def _find(record_type: str, q: Optional[dict] = None) -> list[dict]:
    """Modules read Gold: grade A/B, current version, not quarantined (once governance has run)."""
    from . import governance
    query = {"record_type": record_type, **(q or {})}
    if governance.governed():
        query = governance.gold_query(query)
    return list(get_db().records.find(query, {"_id": 0}))


def as_of() -> datetime:
    dates = [_naive(f.get("as_of")) for f in _find("asset_flow") if f.get("as_of")]
    return max(dates) if dates else datetime.now(timezone.utc).replace(tzinfo=None)


def _days(ev: dict, today: datetime) -> Optional[float]:
    if ev.get("actual_days") is not None and (ev.get("actual_finish") or ev.get("start_date")):
        return max(float(ev["actual_days"]), 0.0)
    start, fin = _naive(ev.get("start_date")), _naive(ev.get("actual_finish"))
    if start and fin:
        return float(max((fin - start).days, 0))
    if start and start <= today:
        return float((today - start).days)
    return None


def _in_gate(ev: dict, today: datetime) -> bool:
    start = _naive(ev.get("start_date"))
    return bool(start and start <= today and not ev.get("actual_finish"))


def _events(q: Optional[dict] = None) -> list[dict]:
    """Gate events with implied finishes: a gate (usually a queue) left open on the
    sheet is finished when a later gate has started."""
    today = as_of()
    by_asset = defaultdict(list)
    for e in _find("gate_event"):
        by_asset[e["asset_id"]].append(e)
    out = []
    for evs in by_asset.values():
        evs.sort(key=lambda e: e.get("gate_seq") or 0)
        for i, e in enumerate(evs):
            if e.get("start_date") and not e.get("actual_finish"):
                later = next((x for x in evs[i + 1:]
                              if x.get("start_date") and _naive(x["start_date"]) <= today), None)
                if later:
                    e["actual_finish"] = later["start_date"]
                    e["implied_finish"] = True
        out.extend(evs)
    if q and "gate" in q:
        out = [e for e in out if e["gate"] == q["gate"]]
    return out


def _trust(entities):
    from . import governance
    return governance.trust_label_for(entities) if governance.governed() else None


def _wip_plan() -> dict:
    return {p["gate"]: p.get("wip_plan") for p in _find("gate_plan")}


def gate_graph(gate: str, last_n: int = LAST_N) -> dict:
    today = as_of()
    events = _events({"gate": gate})
    flows = {f["asset_id"]: f for f in _find("asset_flow")}
    done = sorted((e for e in events if e.get("actual_finish")), key=lambda e: _naive(e["actual_finish"]))
    history = []
    for i, e in enumerate(done):
        d = _days(e, today) or 0.0
        window = [(_days(x, today) or 0.0) for x in done[max(0, i - last_n + 1):i + 1]]
        history.append({"asset_id": e["asset_id"], "actual_days": d, "remaining_days": 0.0,
                        "total_days": d, "requirement": e.get("goal_days"),
                        "last_n_avg": round(mean(window), 1), "finished": _naive(e["actual_finish"]),
                        "in_work": False})
    bars = history[-last_n:]
    current = sorted((e for e in events if _in_gate(e, today)), key=lambda e: _naive(e["start_date"]))
    for e in current:
        d = _days(e, today) or 0.0
        goal = e.get("goal_days") or 0.0
        rem = max(goal - d, 0.0)
        flow = flows.get(e["asset_id"], {})
        bars.append({"asset_id": e["asset_id"], "actual_days": d, "remaining_days": rem,
                     "total_days": d + rem, "requirement": goal, "last_n_avg": None,
                     "started": _naive(e["start_date"]), "projected_finish": _naive(e.get("projected_finish")),
                     "in_work": True, "over_requirement": d > goal,
                     "sell_date": _naive(flow.get("projected_finish"))})
    goals = [e.get("goal_days") for e in events if e.get("goal_days") is not None]
    requirement = max(set(goals), key=goals.count) if goals else None
    plan = _wip_plan().get(gate)
    return {
        "gate": gate, "gate_number": GATE_NUMBER.get(gate), "is_queue": IS_QUEUE.get(gate, False),
        "title": "B-52 PDM Throughput Performance",
        "subtitle": (f"Gate {GATE_NUMBER[gate]}: {gate}" if not IS_QUEUE.get(gate) else f"Queue before gate {GATE_NUMBER[gate]}: {gate}")
        if gate in GATE_NUMBER else gate,
        "last_n": last_n, "as_of": today,
        "last_n_avg": history[-1]["last_n_avg"] if history else None,
        "requirement": requirement,
        "bars": bars,
        "wip": {"plan": plan, "actual": len(current), "over_plan": plan is not None and len(current) > plan},
        "trust": _trust(["gate_event", "asset_flow"]),
    }


def machine(last_n: int = LAST_N) -> dict:
    gates = []
    for g in GATE_ORDER:
        gg = gate_graph(g, last_n)
        gates.append({"gate": g, "is_queue": gg["is_queue"], "last_n_avg": gg["last_n_avg"] or 0.0,
                      "requirement": gg["requirement"] or 0.0, "wip": gg["wip"],
                      "completed": sum(1 for b in gg["bars"] if not b["in_work"])})
    produced = sorted((f for f in _find("asset_flow", {"status": "produced"}) if f.get("actual_finish")),
                      key=lambda f: _naive(f["actual_finish"]))[-last_n:]
    flow = [f["total_flow_days"] for f in produced if f.get("total_flow_days") is not None]
    in_dock = [f["in_dock_flow_days"] for f in produced if f.get("in_dock_flow_days") is not None]
    return {
        "last_n": last_n, "as_of": as_of(), "gates": gates,
        "total_avg_days": round(sum(g["last_n_avg"] for g in gates), 1),
        "total_requirement": round(sum(g["requirement"] for g in gates), 1),
        "last_n_flow_days": round(mean(flow), 1) if flow else None,
        "last_n_in_dock_days": round(mean(in_dock), 1) if in_dock else None,
        "produced": [{"asset_id": f["asset_id"], "finished": _naive(f["actual_finish"]),
                      "flow_days": f.get("total_flow_days"), "location": f.get("location")} for f in produced],
    }


def dock_overview() -> dict:
    today = as_of()
    by_asset = defaultdict(list)
    for e in _events():
        by_asset[e["asset_id"]].append(e)
    cards = []
    for f in _find("asset_flow", {"status": "in_work"}):
        evs = sorted(by_asset.get(f["asset_id"], []), key=lambda e: e.get("gate_seq") or 0)
        cur = next((e for e in reversed(evs) if _in_gate(e, today)), None)
        last_done = next((e for e in reversed(evs) if e.get("actual_finish")), None)
        if cur:
            gate, day, goal = cur["gate"], _days(cur, today), cur.get("goal_days")
        elif last_done:
            nxt = next((n for n in GATE_ORDER[GATE_ORDER.index(last_done["gate"]) + 1:] if IS_QUEUE[n]), None)
            gate = nxt or f"After {last_done['gate']}"
            day = float(max((today - _naive(last_done["actual_finish"])).days, 0))
            goal = 0.0
        else:
            gate, day, goal = "Awaiting induction", None, None
        # a dock is held only from Inspection to Restore; Pre Dock, Post Dock and their queues are off-dock
        dock = next((e.get("dock") for e in reversed(evs) if e.get("dock")), None) if gate in IN_DOCK_GATES else None
        amrep, proj = _naive(f.get("amrep_date")), _naive(f.get("projected_finish"))
        milestones = [{"gate": e["gate"], "date": _naive(e.get("start_date"))}
                      for e in evs if e["gate"] in ("Inspection", "Repair", "Restore") and e.get("start_date")]
        cards.append({
            "asset_id": f["asset_id"], "dock": dock, "gate": gate,
            "day": day, "day_goal": goal, "day_over": day is not None and goal is not None and day > goal,
            "flow_days": f.get("total_flow_days"), "amrep_days": f.get("amrep_days"),
            "in_dock_days": f.get("in_dock_flow_days"), "in_dock_goal": f.get("in_dock_goal_days"),
            "queue_days": f.get("total_queue_days"), "amrep_date": amrep, "projected_finish": proj,
            "amrep_risk": bool(amrep and proj and proj > amrep),
            "start_date": _naive(f.get("start_date")), "milestones": milestones,
            "current_milestone": f.get("current_milestone"),
            "notes": (f.get("attributes") or {}).get("notes", []),
            "data_quality": (f.get("attributes") or {}).get("data_quality"),
        })
    cards.sort(key=lambda c: (GATE_ORDER.index(c["gate"]) if c["gate"] in GATE_ORDER else -1), reverse=True)
    return {"as_of": today, "aircraft": cards, "trust": _trust(["asset_flow"])}


def health_band(mc_rate: Optional[float]) -> dict:
    if mc_rate is None:
        return {"code": None, "label": "Unknown", "status": None}
    v = mc_rate * 100 if mc_rate <= 1 else mc_rate
    for floor, code, label, status in HEALTH_BANDS:
        if v >= floor:
            return {"code": code, "label": label, "status": status}
    return {"code": "NMC", "label": "Non-Mission Capable", "status": "critical"}


def fleet_health() -> dict:
    status = {}
    for s in _find("asset_status"):
        if s.get("mc_rate") is not None or s["asset_id"] not in status:
            status[s["asset_id"]] = s
    subs = defaultdict(list)
    for s in _find("subsystem_health"):
        score = s.get("health_score")
        s["band"] = "good" if score is not None and score > 80 else "warning" if score is not None and score >= 50 else "critical"
        subs[s["asset_id"]].append({k: s.get(k) for k in ("subsystem", "health_score", "critical_fault_count", "band")})
    for v in subs.values():
        v.sort(key=lambda x: (-(x["critical_fault_count"] or 0), x["health_score"] or 0))
    telemetry = defaultdict(list)
    breaches = []
    for t in sorted(_find("sensor_reading"), key=lambda t: _naive(t.get("timestamp")) or datetime.min):
        key = (t.get("asset_id"), t.get("device_id") or t.get("metric"))
        telemetry[key].append({"t": _naive(t.get("timestamp")), "v": t.get("value"),
                               "hi": t.get("threshold_high"), "lo": t.get("threshold_low")})
        v, hi, lo = t.get("value"), t.get("threshold_high"), t.get("threshold_low")
        if v is not None and ((hi is not None and v > hi) or (lo is not None and v < lo)):
            breaches.append({"asset_id": key[0], "sensor": key[1], "value": v, "threshold_high": hi,
                             "threshold_low": lo, "timestamp": _naive(t.get("timestamp"))})
    faults = sorted(_find("fault"), key=lambda f: _naive(f.get("date_logged")) or datetime.min, reverse=True)
    assets = []
    for a, s in status.items():
        assets.append({"asset_id": a, "status": s.get("status"), "mc_rate": s.get("mc_rate"),
                       "band": health_band(s.get("mc_rate")), "mtbm_actual": s.get("mtbm_actual"),
                       "mtbm_target": s.get("mtbm_target"), "alert_count": s.get("alert_count"),
                       "subsystems": subs.get(a, []),
                       "faults": [{k: f.get(k) for k in ("date_logged", "fault_code", "severity", "description", "technician")}
                                  for f in faults if f.get("asset_id") == a]})
    assets.sort(key=lambda x: (x["mc_rate"] is None, x["mc_rate"] or 0))
    rates = [a["mc_rate"] for a in assets if a["mc_rate"] is not None]
    return {
        "fleet_mc_rate": round(mean(rates), 1) if rates else None,
        "assets": assets,
        "telemetry": [{"asset_id": k[0], "sensor": k[1], "points": v} for k, v in telemetry.items()],
        "breaches": breaches,
        "faults": [{k: f.get(k) for k in ("asset_id", "date_logged", "fault_code", "severity", "description", "technician")}
                   for f in faults],
    }


def alerts() -> dict:
    items = []
    for f in _find("fault"):
        sev = (f.get("severity") or "").lower()
        items.append({"level": "critical" if sev == "critical" else "warning", "source": "Fault registry",
                      "asset_id": f.get("asset_id"), "text": f"{f.get('fault_code')}: {f.get('description')}",
                      "when": _naive(f.get("date_logged"))})
    fh = fleet_health()
    seen = set()
    for b in fh["breaches"]:
        k = (b["asset_id"], b["sensor"])
        if k in seen:
            continue
        seen.add(k)
        items.append({"level": "warning", "source": "Telemetry", "asset_id": b["asset_id"],
                      "text": f"{b['sensor']} outside limits ({b['value']})", "when": b["timestamp"]})
    for c in dock_overview()["aircraft"]:
        if c["amrep_risk"]:
            items.append({"level": "critical", "source": "Schedule", "asset_id": c["asset_id"],
                          "text": f"Projected finish {c['projected_finish']:%d %b %Y} is after AMREP {c['amrep_date']:%d %b %Y}",
                          "when": None})
        if c["day_over"]:
            items.append({"level": "warning", "source": "Gate", "asset_id": c["asset_id"],
                          "text": f"{c['gate']} day {c['day']:.0f} of {c['day_goal']:.0f} goal", "when": None})
    order = {"critical": 0, "warning": 1}
    items.sort(key=lambda i: (order[i["level"]], -(i["when"].timestamp() if i["when"] else 0)))
    return {"total": len(items), "critical": sum(i["level"] == "critical" for i in items),
            "warning": sum(i["level"] == "warning" for i in items), "items": items}
