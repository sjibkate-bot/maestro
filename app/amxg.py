"""AMXG specific modules: the analyses behind the AMXG hub screens.

* AMX_01 events()        - one feed of faults, telemetry, schedule/gate risks, issues,
                           blocked work and supply shortages, with acknowledge / assign.
* AMX_02 timelines()     - every aircraft's path through the gates (Gantt rows).
* AMX_03 dock overview   - pdm.dock_overview (existing).
* AMX_04 tiered()        - AFSC -> ALC -> weapon system -> depot line -> dock -> aircraft.
* AMX_05 status_report() - aircraft x gate status grid.
* AMX_06 machine health  - pdm.fleet_health (existing).
* Concepts: dockboard() and bom() icicle trees, gate_script() with found work re-flow
  and a PMSS mirror. The mirror only records what would be sent: PMSS is not connected.

All reads are Gold (pdm._find). User actions (acknowledge, close task, add found work)
are an overlay in their own collection and never change the governed records.
"""
import hashlib
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

from . import pdm
from .db import get_db

LEVELS = ["critical", "serious", "warning", "info"]
SKILL_NAMES = {"AA": "Structures", "AB": "Sheet metal", "AE": "Engines", "AG": "Hydraulics", "AH": "Electrical",
               "AM": "Fuel systems", "AS": "Avionics", "BA": "NDI", "BS": "Corrosion & paint"}
DEPOT_WEAPON_SYSTEM = "B-52"   # the Fixer workbooks are the B-52 PDM line
DEPOT_ORG = "OC-ALC"
TASK_STATES = ["Blocked", "In work", "Not started", "Done"]


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _id(*parts) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:12]


def _audit(action, user, detail):
    from . import governance
    governance.audit(action, user, detail)


def _overlay():
    return get_db().amxg_overlay


# ------------------------------------------------------------------ AMX_01 events

def events() -> dict:
    items = []
    for a in pdm.alerts()["items"]:
        items.append({"source": a["source"], "level": a["level"], "asset_id": a.get("asset_id"),
                      "title": a["text"], "when": a.get("when"), "category": "depot" if a["source"] in ("Schedule", "Gate") else "fleet"})
    for i in pdm._find("issue"):
        st = (i.get("status") or "").lower()
        if st == "stable":
            continue
        items.append({"source": "Issue register", "level": "critical" if st == "critical" else "serious",
                      "asset_id": i.get("weapon_system"), "title": f"{i.get('title')} ({i.get('organization')})",
                      "detail": f"Impact {i.get('impact_days')} days · owner {i.get('owner')}" if i.get("impact_days") else None,
                      "when": pdm._naive(i.get("date_logged")), "category": "enterprise", "ref": i.get("issue_id")})
    for t in _tasks():
        if t["status"] == "Blocked":
            items.append({"source": "Work package", "level": "serious", "asset_id": t["asset_id"],
                          "title": f"{t['task']} blocked" + (f", waiting on {t['part_number']}" if t.get("part_number") else ""),
                          "detail": f"{t['gate']} · {t['work_package']} · skill {t['skill']}", "when": None, "category": "depot"})
    try:
        from . import supply_risk
        for a in supply_risk.overview()["attention"]:
            items.append({"source": "Supply", "level": a["level"] if a["level"] in LEVELS else "warning",
                          "asset_id": a.get("ref"), "title": f"{a['title']}: {a['text']}", "when": None, "category": "supply"})
    except Exception:   # supply data is optional for this feed
        pass
    state = {s["_id"]: s for s in _overlay().find({"kind": "event"})}
    for e in items:
        e["id"] = _id(e["source"], e.get("asset_id"), e["title"])
        s = state.get(e["id"], {})
        e["state"] = s.get("state", "open")
        e["assignee"] = s.get("assignee")
        e["note"] = s.get("note")
        e["updated_by"] = s.get("user")
        e["updated_at"] = s.get("at")
    items.sort(key=lambda e: (e["state"] == "closed", e["state"] == "acknowledged", LEVELS.index(e["level"]),
                              -(e["when"].timestamp() if e.get("when") else 0)))
    open_ = [e for e in items if e["state"] != "closed"]
    return {"as_of": pdm.as_of(), "total": len(items), "open": len(open_),
            "by_level": dict(Counter(e["level"] for e in open_)),
            "by_source": dict(Counter(e["source"] for e in open_)),
            "items": items}


def update_event(event_id: str, state: str, user: str, assignee: Optional[str] = None, note: Optional[str] = None) -> dict:
    if state not in ("open", "acknowledged", "closed"):
        raise ValueError("state must be open, acknowledged or closed")
    doc = {"kind": "event", "state": state, "assignee": assignee, "note": note, "user": user or "unknown", "at": _now()}
    _overlay().update_one({"_id": event_id}, {"$set": doc}, upsert=True)
    _audit(f"event_{state}", doc["user"], {"event": event_id, "assignee": assignee, "note": note})
    return {"id": event_id, **doc}


# ------------------------------------------------------------------ AMX_02 timelines

def _by_asset():
    out = defaultdict(list)
    for e in pdm._events():
        out[e["asset_id"]].append(e)
    for v in out.values():
        v.sort(key=lambda e: e.get("gate_seq") or 0)
    return out


def timelines(kind: str = "all") -> dict:
    today = pdm.as_of()
    evs = _by_asset()
    sched = {s["asset_id"]: s for s in pdm._find("production_schedule")}
    rows = []
    for f in pdm._find("asset_flow"):
        a = f["asset_id"]
        start = pdm._naive(f.get("start_date"))
        status = "delivered" if f.get("status") == "produced" else "incoming" if (start and start > today) else "in_work"
        segs = []
        for e in evs.get(a, []):
            s, fin = pdm._naive(e.get("start_date")), pdm._naive(e.get("actual_finish"))
            if not s or s > today:
                continue
            end = fin or today
            goal = e.get("goal_days")
            days = (end - s).days
            segs.append({"gate": e["gate"], "is_queue": pdm.IS_QUEUE.get(e["gate"], False), "start": s, "end": end,
                         "finished": bool(fin), "implied": bool(e.get("implied_finish")), "days": days, "goal": goal,
                         "over": bool(goal) and days > goal, "dock": e.get("dock")})
        sc = sched.get(a, {})
        amrep = pdm._naive(f.get("amrep_date")) or pdm._naive(sc.get("due_date"))
        proj = pdm._naive(f.get("actual_finish")) or pdm._naive(f.get("projected_finish"))
        cur = next((s for s in reversed(segs) if not s["finished"]), None)
        rows.append({"asset_id": a, "status": status, "start": start or pdm._naive(sc.get("induction_date")),
                     "arrival": pdm._naive(f.get("arrival_date")), "amrep": amrep, "finish": proj,
                     "sold": pdm._naive(sc.get("sold_date")) or pdm._naive(f.get("actual_finish")),
                     "late": bool(amrep and proj and proj > amrep), "current_gate": cur["gate"] if cur else None,
                     "location": f.get("location"), "segments": segs,
                     "milestone": f.get("current_milestone")})
    counts = Counter(r["status"] for r in rows)
    if kind != "all":
        rows = [r for r in rows if r["status"] == kind]
    rows.sort(key=lambda r: r["start"] or datetime.max)
    dates = [d for r in rows for d in (r["start"], r["amrep"], r["finish"]) if d]
    return {"as_of": today, "filter": kind, "counts": {"all": sum(counts.values()), **counts},
            "gates": pdm.GATE_ORDER, "range": [min(dates), max(dates)] if dates else None, "rows": rows,
            "trust": pdm._trust(["gate_event", "asset_flow"])}


# ------------------------------------------------------------------ AMX_04 tiered dashboards

def _status(ratio: Optional[float], good=1.0, warn=0.85) -> str:
    if ratio is None:
        return "unknown"
    return "good" if ratio >= good else "warning" if ratio >= warn else "critical"


def _worst(states) -> str:
    order = ["critical", "serious", "warning", "good", "unknown"]
    states = [s for s in states if s]
    return min(states, key=order.index) if states else "unknown"


def tiered() -> dict:
    """A tree of nodes; each has kpis, a status and children. The UI drills by path."""
    summ = pdm._find("production_summary")
    issues = [i for i in pdm._find("issue") if (i.get("status") or "").lower() != "stable"]

    def issue_list(pred):
        return [{"id": i.get("issue_id"), "title": i.get("title"), "status": i.get("status"),
                 "organization": i.get("organization"), "impact_days": i.get("impact_days")} for i in issues if pred(i)]

    def kpis(rows):
        prod, goal = sum(r.get("produced") or 0 for r in rows), sum(r.get("goal") or 0 for r in rows)
        ha, ht = sum(r.get("hours_actual") or 0 for r in rows), sum(r.get("hours_target") or 0 for r in rows)
        ya = [r["yield_actual"] for r in rows if r.get("yield_actual") is not None]
        yt = [r["yield_target"] for r in rows if r.get("yield_target") is not None]
        ot = [r["on_time_pct"] for r in rows if r.get("on_time_pct") is not None]
        return {"produced": prod, "goal": goal, "attainment": round(prod / goal, 3) if goal else None,
                "hours_actual": ha, "hours_target": ht, "on_time_pct": round(sum(ot) / len(ot), 1) if ot else None,
                "yield_actual": round(sum(ya) / len(ya), 4) if ya else None, "yield_target": round(sum(yt) / len(yt), 4) if yt else None}

    depot = _depot_node()
    orgs = defaultdict(list)
    for r in summ:
        orgs[r.get("organization") or "Unassigned"].append(r)
    if depot and DEPOT_ORG not in orgs:
        orgs[DEPOT_ORG] = []
    org_nodes = []
    for org, rows in sorted(orgs.items()):
        ws_nodes = []
        by_ws = defaultdict(list)
        for r in rows:
            by_ws[r.get("weapon_system") or "Unassigned"].append(r)
        if depot and org == DEPOT_ORG and DEPOT_WEAPON_SYSTEM not in by_ws:
            by_ws[DEPOT_WEAPON_SYSTEM] = []
        for ws, wrows in sorted(by_ws.items()):
            k = kpis(wrows)
            iss = issue_list(lambda i, ws=ws: i.get("weapon_system") == ws)
            periods = sorted(({"period": r.get("period"), "produced": r.get("produced"), "goal": r.get("goal")} for r in wrows),
                             key=lambda p: p["period"] or "")
            children = [depot] if (depot and org == DEPOT_ORG and ws == DEPOT_WEAPON_SYSTEM) else []
            st = _worst([_status(k["attainment"]), "critical" if any(i["status"] == "Critical" for i in iss) else None,
                         children[0]["status"] if children else None])
            ws_nodes.append({"id": f"{org}/{ws}", "level": "Weapon system", "name": ws, "kpis": k, "periods": periods,
                             "issues": iss, "status": st, "children": children,
                             "note": None if children else "No depot line data loaded for this weapon system"})
        k = kpis(rows)
        org_nodes.append({"id": org, "level": "ALC", "name": org, "kpis": k,
                          "issues": issue_list(lambda i, ws_names={n["name"] for n in ws_nodes}: i.get("weapon_system") in ws_names),
                          "status": _worst([n["status"] for n in ws_nodes]), "children": ws_nodes})
    k = kpis(summ)
    root = {"id": "AFSC", "level": "AFSC", "name": "AFSC", "kpis": k, "issues": issue_list(lambda i: True),
            "status": _worst([n["status"] for n in org_nodes]), "children": org_nodes}
    return {"as_of": pdm.as_of(), "root": root, "has_data": bool(summ or depot),
            "trust": pdm._trust(["production_summary", "issue", "gate_event", "asset_flow"])}


def _depot_node() -> Optional[dict]:
    docks = pdm.dock_overview()
    if not docks["aircraft"]:
        return None
    mach = pdm.machine()
    by_dock = defaultdict(list)
    for c in docks["aircraft"]:
        if c["gate"] == "Awaiting induction" or (c.get("start_date") and c["start_date"] > docks["as_of"]):
            continue
        by_dock[c["dock"] or "Off dock"].append(c)
    dock_nodes = []
    for d, cards in sorted(by_dock.items(), key=lambda kv: (kv[0] == "Off dock", kv[0])):
        ac = []
        for c in cards:
            st = "critical" if c["amrep_risk"] else "warning" if c["day_over"] else "good"
            ac.append({"id": c["asset_id"], "level": "Aircraft", "name": c["asset_id"], "status": st, "children": [],
                       "kpis": {"gate": c["gate"], "day": c["day"], "day_goal": c["day_goal"], "flow_days": c["flow_days"],
                                "amrep_days": c["amrep_days"], "amrep_date": c["amrep_date"], "projected_finish": c["projected_finish"]}})
        dock_nodes.append({"id": f"dock/{d}", "level": "Dock", "name": d, "status": _worst([a["status"] for a in ac]),
                           "kpis": {"aircraft": len(ac), "amrep_risk": sum(a["status"] == "critical" for a in ac)},
                           "children": ac})
    over = [g for g in mach["gates"] if g["wip"]["over_plan"]]
    slow = [g for g in mach["gates"] if g["requirement"] and g["last_n_avg"] > g["requirement"]]
    in_work = sum(len(d["children"]) for d in dock_nodes)
    risk = sum(d["kpis"]["amrep_risk"] for d in dock_nodes)
    return {"id": "depot/B-52 PDM", "level": "Depot line", "name": "B-52 PDM line", "children": dock_nodes,
            "status": "critical" if risk else "warning" if (over or slow) else "good",
            "kpis": {"in_work": in_work, "amrep_risk": risk, "flow_avg": mach["last_n_flow_days"],
                     "gate_avg_total": mach["total_avg_days"], "gate_req_total": mach["total_requirement"],
                     "over_plan_gates": [g["gate"] for g in over], "slow_gates": [g["gate"] for g in slow]}}


# ------------------------------------------------------------------ AMX_05 aircraft status report

def status_report() -> dict:
    today = pdm.as_of()
    evs = _by_asset()
    flows = {f["asset_id"]: f for f in pdm._find("asset_flow")}
    health = {a["asset_id"]: a for a in pdm.fleet_health()["assets"]}
    tasks = defaultdict(list)
    for t in _tasks():
        tasks[t["asset_id"]].append(t)
    rows = []
    for a, f in flows.items():
        cells = {}
        for e in evs.get(a, []):
            s, fin = pdm._naive(e.get("start_date")), pdm._naive(e.get("actual_finish"))
            goal = e.get("goal_days")
            if not s or s > today:
                state, days = "planned", None
            else:
                days = ((fin or today) - s).days
                over = bool(goal) and days > goal   # queues carry a goal of 0: no target to miss
                state = ("done_over" if fin and over else "done" if fin else "in_work_over" if over else "in_work")
            cells[e["gate"]] = {"state": state, "days": days, "goal": goal, "start": s, "finish": fin,
                                "projected": pdm._naive(e.get("projected_finish")), "dock": e.get("dock")}
        amrep, proj = pdm._naive(f.get("amrep_date")), pdm._naive(f.get("actual_finish")) or pdm._naive(f.get("projected_finish"))
        h = health.get(a, {})
        tk = tasks.get(a, [])
        status = "delivered" if f.get("status") == "produced" else "incoming" if (pdm._naive(f.get("start_date")) or today) > today else "in_work"
        rows.append({"asset_id": a, "status": status, "location": f.get("location"), "cells": cells,
                     "amrep": amrep, "finish": proj, "late": bool(amrep and proj and proj > amrep),
                     "margin_days": (amrep - proj).days if amrep and proj else None,
                     "flow_days": f.get("total_flow_days"), "amrep_days": f.get("amrep_days"),
                     "mc_rate": h.get("mc_rate"), "band": h.get("band"), "open_faults": len(h.get("faults") or []),
                     "tasks": dict(Counter(t["status"] for t in tk)),
                     "blocked_parts": sorted({t["part_number"] for t in tk if t["status"] == "Blocked" and t.get("part_number")}),
                     "notes": (f.get("attributes") or {}).get("notes", [])})
    order = {"in_work": 0, "incoming": 1, "delivered": 2}
    rows.sort(key=lambda r: (order[r["status"]], r["margin_days"] if r["margin_days"] is not None else 9999))
    return {"as_of": today, "gates": pdm.GATE_ORDER, "rows": rows, "counts": dict(Counter(r["status"] for r in rows)),
            "trust": pdm._trust(["gate_event", "asset_flow"])}


# ------------------------------------------------------------------ work tasks (+ overlay)

def _tasks(asset: Optional[str] = None) -> list[dict]:
    q = {"asset_id": asset} if asset else None
    tasks = [dict(t) for t in pdm._find("work_task", q)]
    ov = list(_overlay().find({"kind": {"$in": ["found_work", "closure"]}, **({"asset_id": asset} if asset else {})}))
    closed = {o["task_id"]: o for o in ov if o["kind"] == "closure"}
    for o in ov:
        if o["kind"] == "found_work":
            tasks.append({**o["task"], "overlay": True})
    for t in tasks:
        t["skill_name"] = SKILL_NAMES.get(t.get("skill"), t.get("skill"))
        t["found"] = str(t.get("found_work") or "").strip().lower() in ("yes", "y", "true", "1")
        if t.get("task_id") in closed:
            t["status"] = "Done"
            t["closed_by"] = closed[t["task_id"]]["user"]
            t["closed_date"] = closed[t["task_id"]]["at"]
        if t.get("status") not in TASK_STATES:
            t["status"] = "In work" if t.get("status") else "Not started"
    return tasks


# ------------------------------------------------------------------ concept: dockboard icicle

def dockboard() -> dict:
    """floor > dock > aircraft > gate > skill > work package > task. Leaf value = planned hours.
    Leaves carry every lens attribute; the UI rolls them up (worst-of / sums)."""
    today = pdm.as_of()
    cards = {c["asset_id"]: c for c in pdm.dock_overview()["aircraft"]}
    skill_load = defaultdict(float)
    tasks = _tasks()
    for t in tasks:
        if t["status"] != "Done":
            skill_load[t["skill"]] += max((t.get("hours_planned") or 0) - (t.get("hours_actual") or 0), 0)
    top = max(skill_load.values()) if skill_load else 1
    root = {"name": "Floor", "level": "Floor", "children": {}}
    for t in tasks:
        c = cards.get(t["asset_id"], {})
        day = c.get("day") or 0
        end = (t.get("planned_start_day") or 1) + (t.get("planned_days") or 1) - 1
        sched = ("late" if t["status"] != "Done" and day > end else
                 "at_risk" if t["status"] == "Not started" and day >= (t.get("planned_start_day") or 1) else "on_time")
        hp, ha = t.get("hours_planned") or 0, t.get("hours_actual") or 0
        leaf = {"name": t["task"], "level": "Task", "value": hp or 1, "id": t.get("task_id"),
                "attrs": {"status": t["status"], "schedule": sched,
                          "priority": "high" if (c.get("amrep_risk") or t["found"]) else "normal",
                          "parts": "short" if t["status"] == "Blocked" and t.get("part_number") else "needs" if t.get("part_number") else "none",
                          "hours_planned": hp, "hours_actual": ha,
                          "manning": round(skill_load[t["skill"]] / top, 2) if top else 0,
                          "part_number": t.get("part_number"), "found": t["found"], "skill": t["skill"],
                          "day": day, "planned_end": end}}
        path = [("Dock", c.get("dock") or "Off dock"), ("Aircraft", t["asset_id"]), ("Gate", t["gate"]),
                ("Skill", f"{t['skill']} {t['skill_name']}"), ("Work package", t["work_package"])]
        node = root
        for lvl, name in path:
            node = node["children"].setdefault(name, {"name": name, "level": lvl, "children": {}})
        node["children"][leaf["id"] or leaf["name"]] = leaf

    def finish(n):
        if "children" in n:
            n["children"] = [finish(x) for x in n["children"].values()]
            n["children"].sort(key=lambda x: x["name"])
        return n
    return {"as_of": today, "tree": finish(root), "tasks": len(tasks),
            "skill_load": {k: round(v) for k, v in sorted(skill_load.items(), key=lambda kv: -kv[1])},
            "lenses": ["status", "schedule", "priority", "parts", "hours", "manning"],
            "trust": pdm._trust(["work_task"])}


# ------------------------------------------------------------------ concept: BOM icicle

def bom(asset: Optional[str] = None) -> dict:
    lines = pdm._find("bom_line")
    if not lines:
        return {"tree": None, "has_data": False}
    items, sups = {}, {}
    try:
        from . import supply_risk
        m = supply_risk.build()
        items = m["items"]
        sups = {s["supplier"]: s for s in m["suppliers"].values()}
    except Exception:
        pass
    costs = {p.get("sku"): p.get("unit_cost") for p in pdm._find("product")}
    tasks = _tasks(asset)
    need = defaultdict(list)
    for t in tasks:
        if t.get("part_number") and t["status"] != "Done":
            need[t["part_number"]].append({"asset_id": t["asset_id"], "task": t["task"], "status": t["status"]})
    kids = defaultdict(list)
    desc = {}
    parents, children = set(), set()
    for l in lines:
        kids[l["parent"]].append(l)
        desc[l["child"]] = l.get("description")
        parents.add(l["parent"])
        children.add(l["child"])
    roots = sorted(parents - children) or sorted(parents)[:1]

    def leaf_attrs(pn, qty):
        it = items.get(pn, {})
        sup = sups.get((it.get("suppliers") or [None])[0]) if it.get("suppliers") else None
        cover = it.get("days_cover")
        blocked = any(n["status"] == "Blocked" for n in need.get(pn, []))
        avail = ("blocked" if blocked or (it.get("on_hand") == 0) else "short" if cover is not None and cover < 14 else
                 "ok" if it else "unknown")
        cost = costs.get(pn) or it.get("unit_cost")
        otr = sup.get("on_time_rate") if sup else None
        return {"availability": avail, "days_cover": cover, "on_hand": it.get("on_hand"),
                "lead_time": sup.get("lead_time_days") if sup else None, "supplier": sup.get("supplier") if sup else None,
                "unit_cost": cost, "ext_cost": round(cost * qty, 2) if cost else None,
                "conformance": otr, "conformance_basis": "supplier on-time delivery" if otr is not None else None,
                "sole_sourced": it.get("sole_sourced"), "needed_by": need.get(pn, [])}

    def build(pn, qty=1, depth=0, seen=()):
        node = {"name": pn, "description": desc.get(pn) or pn, "qty": qty, "level": depth}
        if pn in kids and pn not in seen:
            node["children"] = [build(l["child"], l.get("quantity") or 1, depth + 1, seen + (pn,))
                                for l in sorted(kids[pn], key=lambda l: l["child"])]
        else:
            node["attrs"] = leaf_attrs(pn, qty)
            node["value"] = 1
        return node

    tree = build(roots[0]) if len(roots) == 1 else {"name": "BOM", "description": "All end items", "level": 0,
                                                    "children": [build(r, 1, 1) for r in roots]}
    assets = sorted({t["asset_id"] for t in _tasks() if t.get("part_number")})
    return {"has_data": True, "tree": tree, "asset": asset, "assets": assets,
            "lenses": ["availability", "lead_time", "cost", "conformance"],
            "trust": pdm._trust(["bom_line", "inventory", "purchase_order"])}


# ------------------------------------------------------------------ concept: gate script

def gate_script(asset: str, gate: Optional[str] = None) -> dict:
    cards = {c["asset_id"]: c for c in pdm.dock_overview()["aircraft"]}
    c = cards.get(asset, {})
    tasks = _tasks(asset)
    gates = sorted({t["gate"] for t in tasks}, key=lambda g: pdm.GATE_ORDER.index(g) if g in pdm.GATE_ORDER else 99)
    gate = gate or (c.get("gate") if c.get("gate") in gates else gates[0] if gates else None)
    tasks = [t for t in tasks if t["gate"] == gate]
    goal = next((e.get("goal_days") for e in _by_asset().get(asset, []) if e["gate"] == gate), None)
    day = c.get("day") if c.get("gate") == gate else None
    # re-flow: found work occupies its work package; a not-started task of that package that would start
    # while the found work is open is pushed to the day after it ends (later found work can push it again)
    rows = []
    for t in sorted(tasks, key=lambda t: (t.get("planned_start_day") or 0, t["skill"])):
        rows.append({**{k: t.get(k) for k in ("task_id", "task", "skill", "skill_name", "work_package", "status", "part_number",
                                              "hours_planned", "hours_actual", "found", "overlay", "closed_by", "closed_date")},
                     "start": t.get("planned_start_day") or 1, "days": t.get("planned_days") or 1, "shift": 0})
    for r in rows:
        r["new_start"] = r["start"]
    for f in sorted((r for r in rows if r["found"]), key=lambda r: r["start"]):
        f_end = f["new_start"] + f["days"] - 1
        for r in sorted(rows, key=lambda r: r["new_start"]):
            if (not r["found"] and r["work_package"] == f["work_package"] and r["status"] == "Not started"
                    and f["new_start"] <= r["new_start"] <= f_end):
                r["new_start"] = f_end + 1
    for r in rows:
        r["shift"] = r["new_start"] - r["start"]
        r["end"] = r["new_start"] + r["days"] - 1
        r["late"] = day is not None and r["status"] != "Done" and day > r["end"]
    planned_end = max((r["start"] + r["days"] - 1 for r in rows if not r["found"]), default=0)
    reflowed_end = max((r["end"] for r in rows), default=0)
    mirror = sorted(_overlay().find({"kind": {"$in": ["closure", "found_work"]}, "asset_id": asset}, {"_id": 0}),
                    key=lambda o: o["at"], reverse=True)
    for t in tasks:   # closures and found work already in the source data also appear in the mirror
        if t.get("overlay"):
            continue
        if t["found"]:
            mirror.append({"kind": "found_work", "asset_id": asset, "task_id": t["task_id"], "user": "source data",
                           "at": None, "message": f"Scope advisory: {t['task']} added to {t['work_package']}"})
    return {"asset_id": asset, "gate": gate, "gates": gates, "goal_days": goal, "day": day,
            "planned_end": planned_end, "reflowed_end": reflowed_end,
            "over_goal": goal is not None and reflowed_end > goal,
            "skills": {s: SKILL_NAMES.get(s, s) for s in sorted({r["skill"] for r in rows})},
            "rows": rows, "mirror": mirror,
            "assets": sorted({t["asset_id"] for t in _tasks()}),
            "pmss_connected": False}


def close_task(asset: str, task_id: str, user: str) -> dict:
    if not any(t.get("task_id") == task_id for t in _tasks(asset)):
        raise KeyError(task_id)
    t = next(t for t in _tasks(asset) if t.get("task_id") == task_id)
    doc = {"kind": "closure", "asset_id": asset, "task_id": task_id, "user": user or "unknown", "at": _now(),
           "message": f"Closure: {t['task']} ({task_id})"}
    _overlay().insert_one(dict(doc))
    _audit("task_closed", doc["user"], {"asset": asset, "task": task_id})
    return doc


def add_found_work(asset: str, gate: str, work_package: str, task: str, skill: str, start_day: int, days: int,
                   hours: float, user: str, part_number: Optional[str] = None) -> dict:
    if not task or not skill or days < 1 or start_day < 1:
        raise ValueError("task, skill, start day and days are required")
    n = _overlay().count_documents({"kind": "found_work", "asset_id": asset}) + 1
    tid = f"{asset}-FW-{n:03d}"
    t = {"asset_id": asset, "gate": gate, "skill": skill.upper(), "work_package": work_package or "Found work",
         "task": task, "task_id": tid, "planned_start_day": int(start_day), "planned_days": int(days),
         "hours_planned": hours, "hours_actual": 0, "status": "Not started", "found_work": "yes", "part_number": part_number}
    doc = {"kind": "found_work", "asset_id": asset, "task_id": tid, "task": t, "user": user or "unknown", "at": _now(),
           "message": f"Scope advisory: {task} added to {t['work_package']} ({days} days, {hours:g} h)"}
    _overlay().insert_one(dict(doc))
    _audit("found_work_added", doc["user"], {"asset": asset, "task": tid})
    doc.pop("_id", None)
    return doc


def hub() -> dict:
    """Card status and a headline number per module."""
    db = get_db()
    has = lambda rt: db.records.count_documents({"record_type": rt}, limit=1) > 0
    ev = events()
    tl = timelines()
    return {"as_of": pdm.as_of(), "cards": {
        "events": {"live": True, "value": ev["open"], "label": "open events", "critical": ev["by_level"].get("critical", 0)},
        "timelines": {"live": has("gate_event"), "value": tl["counts"].get("in_work", 0), "label": "aircraft in work"},
        "docks": {"live": has("asset_flow"), "value": len(pdm.dock_overview()["aircraft"]), "label": "aircraft"},
        "tiered": {"live": has("production_summary") or has("gate_event"), "value": len({r.get("organization") for r in pdm._find("production_summary")}), "label": "ALCs"},
        "status": {"live": has("asset_flow"), "value": sum(r["late"] for r in tl["rows"]), "label": "AMREP risk"},
        "machine": {"live": has("asset_status"), "value": pdm.fleet_health()["fleet_mc_rate"], "label": "fleet MC %"},
        "dockboard": {"live": has("work_task"), "value": db.records.count_documents({"record_type": "work_task"}), "label": "tasks"},
        "bom": {"live": has("bom_line"), "value": db.records.count_documents({"record_type": "bom_line"}), "label": "BOM lines"},
        "script": {"live": has("work_task"), "value": len({t["asset_id"] for t in _tasks()}), "label": "aircraft scripted"},
    }}
