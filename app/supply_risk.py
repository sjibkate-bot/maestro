"""Supply risk analysis from governed (Gold) supply data.

Everything is derived from data loaded through Configuration: supplier master,
purchase orders, inventory, item master and customer orders. Each number states
what it is built from, so a rating can be explained and challenged.

Supplier rating = likelihood (1-5) x impact (1-5), as in common SCRM practice:
  likelihood <- delivery performance, lead time, country concentration of spend
  impact     <- share of spend, sole-sourced items, days of cover of its items
A sub-tier supplier inherits the impact of the supplier it feeds.
"""
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from statistics import mean
from typing import Any, Optional

from .db import get_db
from .models import now

BANDS = [(16, "Critical", "critical"), (10, "High", "serious"), (5, "Moderate", "warning"), (0, "Low", "good")]
STRATEGIES = ["Treat", "Transfer", "Tolerate", "Terminate"]
PLAN_STATUSES = ["Proposed", "Approved", "In progress", "Done"]
SEGMENTS = {
    "Strategic": "Partner: dual-source critical inputs, joint continuity plans, map sub-tiers.",
    "Bottleneck": "Secure supply: buffer stock, pre-qualified alternates, longer agreements.",
    "Leverage": "Use buying power: competitive bids, consolidate volume.",
    "Non-critical": "Simplify: catalogue buying, automation, fewer suppliers.",
}


# ---------------------------------------------------------------- helpers

def _naive(d):
    return d.replace(tzinfo=None) if isinstance(d, datetime) and d.tzinfo else d


def _today() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _gold(record_type: str) -> list[dict]:
    from . import governance
    q = {"record_type": record_type}
    if governance.governed():
        q = governance.gold_query(q)
    return list(get_db().records.find(q, {"_id": 0, "cleaning": 0}))


def _band(score_0_100: float) -> int:
    return 1 if score_0_100 < 20 else 2 if score_0_100 < 40 else 3 if score_0_100 < 60 else 4 if score_0_100 < 80 else 5


def rating_class(r: int) -> dict:
    for floor, label, status in BANDS:
        if r >= floor:
            return {"label": label, "status": status}
    return {"label": "Low", "status": "good"}


def _key(s) -> str:
    return str(s).strip().lower() if s is not None else ""


def _yes(v) -> bool:
    return _key(v) in ("yes", "y", "true", "1", "sole", "single", "sole source", "single source")


def _line_value(r: dict) -> float:
    if r.get("total_value"):
        return float(r["total_value"])
    return float((r.get("quantity") or 0) * (r.get("unit_cost") or 0))


# ---------------------------------------------------------------- the model

def build() -> dict:
    """Compute items, suppliers and their ratings in one pass (shared by every view)."""
    today = _today()
    master = {}
    for s in _gold("supplier"):
        master[_key(s["supplier"])] = s
    pos = _gold("purchase_order")
    inv = _gold("inventory")
    products = _gold("product")
    sos = _gold("sales_order")

    # ---- items: who supplies them, stock, demand, cover
    item_sup = defaultdict(set)
    names = {}
    for p in pos:
        if p.get("sku") and p.get("supplier"):
            item_sup[p["sku"]].add(_key(p["supplier"]))
            names[_key(p["supplier"])] = p["supplier"]
    for p in products:
        if p.get("sku") and p.get("supplier"):
            item_sup[p["sku"]].add(_key(p["supplier"]))
            names[_key(p["supplier"])] = p["supplier"]
    for k, s in master.items():
        names[k] = s["supplier"]

    stock = defaultdict(lambda: {"on_hand": 0.0, "reorder_point": 0.0, "sites": set(), "unit_cost": None, "description": None})
    for r in inv:
        if not r.get("sku"):
            continue
        st = stock[r["sku"]]
        st["on_hand"] += r.get("quantity") or 0
        st["reorder_point"] += r.get("reorder_point") or 0
        if r.get("location"):
            st["sites"].add(str(r["location"]))
        st["unit_cost"] = st["unit_cost"] or r.get("unit_cost")
        st["description"] = st["description"] or r.get("description")
    for p in products:
        if p.get("sku") in stock:
            stock[p["sku"]]["description"] = stock[p["sku"]]["description"] or p.get("description")

    def daily_rate(lines, field="order_date"):
        by = defaultdict(float)
        dates = defaultdict(list)
        for x in lines:
            if x.get("sku") and x.get("quantity") and _naive(x.get(field)):
                by[x["sku"]] += x["quantity"]
                dates[x["sku"]].append(_naive(x[field]))
        span_all = [d for v in dates.values() for d in v]
        if not span_all:
            return {}
        span = max((max(span_all) - min(span_all)).days + 1, 30)
        return {k: v / span for k, v in by.items()}

    demand_so = daily_rate(sos)
    demand_po = daily_rate(pos)
    customers = defaultdict(set)
    for s in sos:
        if s.get("sku") and s.get("customer"):
            customers[s["sku"]].add(str(s["customer"]))

    open_po = defaultdict(lambda: {"qty": 0.0, "next": None})
    for p in pos:
        if p.get("sku") and not p.get("delivered_date"):
            o = open_po[p["sku"]]
            o["qty"] += p.get("quantity") or 0
            pr = _naive(p.get("promised_date"))
            if pr and (o["next"] is None or pr < o["next"]):
                o["next"] = pr

    items = {}
    for sku in set(stock) | set(item_sup):
        st = stock.get(sku, {"on_hand": None, "reorder_point": None, "sites": set(), "unit_cost": None, "description": None})
        d, basis = demand_so.get(sku), "customer orders"
        if d is None:
            d, basis = demand_po.get(sku), "purchase orders (proxy)"
        cover = (st["on_hand"] / d) if (d and st["on_hand"] is not None) else None
        sups = sorted(item_sup.get(sku, set()))
        items[sku] = {
            "sku": sku, "description": st.get("description"), "on_hand": st["on_hand"],
            "reorder_point": st["reorder_point"], "sites": sorted(st["sites"]), "unit_cost": st.get("unit_cost"),
            "daily_demand": round(d, 2) if d else None, "demand_basis": basis if d else None,
            "days_cover": round(cover, 1) if cover is not None else None,
            "suppliers": [names.get(s, s) for s in sups], "sole_sourced": len(sups) == 1,
            "open_po_qty": open_po[sku]["qty"] if sku in open_po else 0, "next_receipt": open_po[sku]["next"] if sku in open_po else None,
            "customers": sorted(customers.get(sku, set())),
            "below_reorder": st["reorder_point"] is not None and st["on_hand"] is not None and st["reorder_point"] > 0 and st["on_hand"] <= st["reorder_point"],
        }
        if cover is not None:
            items[sku]["stockout_date"] = today + timedelta(days=cover)

    # ---- suppliers
    lines_by = defaultdict(list)
    for p in pos:
        if p.get("supplier"):
            lines_by[_key(p["supplier"])].append(p)
    all_keys = set(master) | set(lines_by)
    spend = {}
    spend_basis = {}
    for k in all_keys:
        m = master.get(k, {})
        if m.get("annual_spend"):
            spend[k], spend_basis[k] = float(m["annual_spend"]), "annual spend (supplier master)"
        else:
            spend[k], spend_basis[k] = sum(_line_value(x) for x in lines_by.get(k, [])), "purchase-order value"
    total_spend = sum(spend.values()) or 1.0
    by_country = defaultdict(float)
    for k in all_keys:
        c = master.get(k, {}).get("country")
        if c:
            by_country[c] += spend[k]
    known_country_spend = sum(by_country.values()) or 1.0

    sups: dict[str, dict] = {}
    for k in all_keys:
        m = master.get(k, {})
        lines = lines_by.get(k, [])
        delivered = [x for x in lines if _naive(x.get("delivered_date")) and _naive(x.get("promised_date"))]
        late = [x for x in delivered if _naive(x["delivered_date"]) > _naive(x["promised_date"])]
        open_late = [x for x in lines if not x.get("delivered_date") and _naive(x.get("promised_date")) and _naive(x["promised_date"]) < today]
        on_time = (1 - len(late) / len(delivered)) if delivered else None
        avg_late = mean((_naive(x["delivered_date"]) - _naive(x["promised_date"])).days for x in late) if late else 0.0
        lts = [(_naive(x["delivered_date"]) - _naive(x["order_date"])).days for x in delivered if _naive(x.get("order_date"))]
        lead = mean(lts) if lts else (m.get("lead_time_days") if m.get("lead_time_days") is not None else None)
        skus = sorted(s for s, ss in item_sup.items() if k in ss)
        sole_items = [s for s in skus if items.get(s, {}).get("sole_sourced")]
        if _yes(m.get("sole_source")) and not sole_items:
            sole_items = skus or ([m.get("commodity")] if m.get("commodity") else [])
        covers = [items[s]["days_cover"] for s in skus if items.get(s, {}).get("days_cover") is not None]
        sole_covers = [items[s]["days_cover"] for s in sole_items if items.get(s, {}).get("days_cover") is not None]
        tier = int(m["tier"]) if m.get("tier") else (1 if lines else None)
        country = m.get("country")
        # likelihood factors (0-100, higher = riskier); None = no data
        f_delivery = None if on_time is None else min(100.0, (1 - on_time) * 70 + min(avg_late, 30) + 10 * len(open_late))
        f_lead = None if lead is None else min(100.0, lead / 60 * 100)
        f_conc = None if not country else min(100.0, by_country[country] / known_country_spend * 100 * 1.5)
        lik_parts = [(f_delivery, .5), (f_lead, .2), (f_conc, .3)]
        have = [(v, w) for v, w in lik_parts if v is not None]
        lik = sum(v * w for v, w in have) / sum(w for _, w in have) if have else 50.0
        # impact factors
        share = spend[k] / total_spend
        f_spend = min(100.0, share * 250)
        mc = min(covers) if covers else None
        cover_risk = lambda c: 50.0 if c is None else 100.0 if c <= 7 else 75.0 if c <= 14 else 50.0 if c <= 30 else 20.0
        # losing a sole source stops its items; other items have another supplier to fall back on
        f_cont = max(cover_risk(min(sole_covers) if sole_covers else (None if sole_items else 0)) if sole_items else 0.0,
                     cover_risk(mc) * 0.6)
        imp = .4 * f_spend + .6 * f_cont
        conf_parts = [f_delivery is not None, f_lead is not None, f_conc is not None, mc is not None, bool(m)]
        sups[k] = {
            "key": k, "supplier": names.get(k, k), "tier": tier, "country": country, "supplies_to": m.get("supplies_to"),
            "commodity": m.get("commodity"), "spend": round(spend[k], 2), "spend_basis": spend_basis[k],
            "spend_share": round(share, 4), "po_lines": len(lines), "delivered_lines": len(delivered),
            "on_time_rate": round(on_time, 3) if on_time is not None else None, "avg_days_late": round(avg_late, 1),
            "open_late": len(open_late), "lead_time_days": round(lead, 1) if lead is not None else None,
            "items": skus, "sole_items": sole_items, "min_days_cover": mc, "in_master": bool(m),
            "factors": {"delivery": f_delivery, "lead_time": f_lead, "concentration": f_conc,
                        "spend_share": f_spend, "continuity": f_cont},
            "likelihood_score": round(lik, 1), "impact_score": round(imp, 1),
            "confidence": round(sum(conf_parts) / len(conf_parts), 2),
        }
    # sub-tiers inherit the impact of the supplier they feed
    for k, s in sups.items():
        parent = sups.get(_key(s.get("supplies_to"))) if s.get("supplies_to") else None
        if parent and (s["tier"] or 1) >= 2:
            s["impact_score"] = max(s["impact_score"], parent["impact_score"])
            s["inherits_from"] = parent["supplier"]
    # segmentation is relative: split at the median of this supplier base
    lik_med = sorted(x["likelihood_score"] for x in sups.values())[len(sups) // 2] if sups else 50
    imp_med = sorted(x["impact_score"] for x in sups.values())[len(sups) // 2] if sups else 50
    for s in sups.values():
        L, I = _band(s["likelihood_score"]), _band(s["impact_score"])
        r = L * I
        s.update({"likelihood": L, "impact": I, "rating": r, "rating_class": rating_class(r),
                  "uncertainty": round((1 - s["confidence"]) * 4),
                  "value_at_risk": round(L / 5 * s["spend"], 2),
                  "segment": ("Strategic" if s["likelihood_score"] >= lik_med and s["impact_score"] >= imp_med else
                              "Bottleneck" if s["likelihood_score"] >= lik_med else
                              "Leverage" if s["impact_score"] >= imp_med else "Non-critical")})
        s["actions"] = _actions(s, items)
        s["explain"] = _explain(s)
    return {"suppliers": sups, "items": items, "total_spend": total_spend, "by_country": dict(by_country),
            "split": {"likelihood": lik_med, "impact": imp_med},
            "has": {"master": bool(master), "po": bool(pos), "inventory": bool(inv), "demand": bool(sos), "products": bool(products)}}


def _actions(s: dict, items: dict) -> list[str]:
    out = []
    if s["sole_items"] and s["rating"] >= 10:
        out.append(f"Qualify a second source for {', '.join(map(str, s['sole_items'][:3]))}")
    if s["min_days_cover"] is not None and s["min_days_cover"] < 14:
        out.append(f"Raise safety stock: lowest cover is {s['min_days_cover']:.0f} days")
    if s["on_time_rate"] is not None and s["on_time_rate"] < 0.8:
        out.append(f"Supplier development: on-time {s['on_time_rate'] * 100:.0f}%, avg {s['avg_days_late']:.0f} days late")
    if s["open_late"]:
        out.append(f"Expedite {s['open_late']} overdue open line(s)")
    if s["factors"]["concentration"] and s["factors"]["concentration"] >= 60:
        out.append(f"Diversify: high share of spend in {s['country']}")
    if (s["tier"] or 1) >= 2:
        out.append(f"Map continuity with {s.get('supplies_to') or 'the tier-1 it feeds'}")
    if not s["in_master"]:
        out.append("Add to the supplier master (country, tier, sourcing) to raise confidence")
    return out


def _explain(s: dict) -> list[dict]:
    f = s["factors"]
    fmt = lambda v: "no data" if v is None else f"{v:.0f}"
    return [
        {"step": "What it supplies", "value": s.get("commodity") or (f"{len(s['items'])} item(s)" if s["items"] else "unknown"),
         "detail": f"Tier {s['tier'] or '?'}; {len(s['sole_items'])} sole-sourced item(s)"
                   + (f"; feeds {s['supplies_to']}" if s.get("supplies_to") else "")},
        {"step": "Where it is", "value": s.get("country") or "unknown",
         "detail": f"Concentration risk {fmt(f['concentration'])}"},
        {"step": "Performance", "value": f"L{s['likelihood']}",
         "detail": (f"On-time {s['on_time_rate'] * 100:.0f}% of {s['delivered_lines']} lines; "
                    if s["on_time_rate"] is not None else "No received lines; ")
                   + (f"lead {s['lead_time_days']:.0f} d" if s["lead_time_days"] is not None else "lead time unknown")},
        {"step": "Continuity", "value": f"I{s['impact']}",
         "detail": f"{s['spend_share'] * 100:.1f}% of spend; lowest cover "
                   + (f"{s['min_days_cover']:.0f} d" if s["min_days_cover"] is not None else "unknown")
                   + (f"; inherits from {s['inherits_from']}" if s.get("inherits_from") else "")},
    ]


# ---------------------------------------------------------------- views

def overview() -> dict:
    m = build()
    sups = list(m["suppliers"].values())
    from . import governance
    trust = [p for p in governance.products() if p["domain"] == "supply" and p["records"]] if governance.governed() else []
    conf = round(mean(p["score"] for p in trust), 1) if trust else None
    weighted = sum(s["rating"] / 25 * 100 * s["spend"] for s in sups)
    index = round(weighted / (sum(s["spend"] for s in sups) or 1), 0) if sups else None
    items = list(m["items"].values())
    attention = []
    for s in sorted(sups, key=lambda s: -s["rating"])[:5]:
        if s["rating"] >= 10:
            attention.append({"kind": "supplier", "level": s["rating_class"]["status"], "title": s["supplier"],
                              "text": f"{s['rating_class']['label']} risk {s['rating']}/25 · " + (s["actions"][0] if s["actions"] else ""),
                              "ref": s["supplier"]})
    for it in sorted([i for i in items if i["days_cover"] is not None and i["days_cover"] < 14], key=lambda i: i["days_cover"])[:5]:
        attention.append({"kind": "item", "level": "critical" if it["days_cover"] < 7 else "serious", "title": it["sku"],
                          "text": f"{it['days_cover']:.0f} days of cover" + (" · sole-sourced" if it["sole_sourced"] else "")
                                  + (f" · next receipt {it['next_receipt']:%d %b}" if it.get("next_receipt") else " · no open order"),
                          "ref": it["sku"]})
    return {
        "has_data": bool(sups or items), "has": m["has"],
        "kpis": {
            "risk_index": index, "suppliers": len(sups),
            "critical": sum(s["rating_class"]["label"] == "Critical" for s in sups),
            "high": sum(s["rating_class"]["label"] == "High" for s in sups),
            "value_at_risk": round(sum(s["value_at_risk"] for s in sups if s["rating"] >= 10), 0),
            "spend": round(m["total_spend"], 0) if sups else 0,
            "spofs": sum(1 for i in items if i["sole_sourced"]) + sum(1 for s in sups if (s["tier"] or 1) >= 2 and s["sole_items"]),
            "low_cover": sum(1 for i in items if i["days_cover"] is not None and i["days_cover"] < 14),
            "items": len(items), "data_confidence": conf,
            "trust": [{"name": p["name"], "label": p["trust_label"], "id": p["id"]} for p in trust],
        },
        "attention": attention,
        "distribution": dict(Counter(s["rating_class"]["label"] for s in sups)),
    }


def suppliers() -> list[dict]:
    m = build()
    return sorted(m["suppliers"].values(), key=lambda s: (-s["rating"], -s["spend"]))


def supplier(name: str) -> dict:
    m = build()
    s = m["suppliers"].get(_key(name))
    if not s:
        raise KeyError(name)
    s = dict(s)
    s["item_detail"] = [m["items"][i] for i in s["items"] if i in m["items"]]
    s["sub_tiers"] = [x["supplier"] for x in m["suppliers"].values() if _key(x.get("supplies_to")) == s["key"]]
    s["plans"] = plans(supplier=s["supplier"])
    return s


def network() -> dict:
    m = build()
    sups, items = m["suppliers"], m["items"]
    nodes, edges = [], []

    def add(nid, kind, label, col, **kw):
        nodes.append({"id": nid, "kind": kind, "label": label, "column": col, **kw})

    for k, s in sups.items():
        col = "Sub-tier" if (s["tier"] or 1) >= 2 else "Tier 1"
        add("s:" + k, "supplier", s["supplier"], col, rating=s["rating"], status=s["rating_class"]["status"],
            klass=s["rating_class"]["label"], country=s["country"], spof=bool(s["sole_items"]) and ((s["tier"] or 1) >= 2 or bool(s["sole_items"])),
            found="master" if s["in_master"] else "orders")
        if s.get("supplies_to") and _key(s["supplies_to"]) in sups:
            edges.append({"from": "s:" + k, "to": "s:" + _key(s["supplies_to"])})
    sites, custs = set(), set()
    for sku, it in items.items():
        if not it["suppliers"] and not it["sites"]:
            continue
        add("i:" + sku, "item", sku, "Items", spof=it["sole_sourced"], cover=it["days_cover"],
            status="critical" if it["days_cover"] is not None and it["days_cover"] < 7 else
                   "serious" if it["days_cover"] is not None and it["days_cover"] < 14 else "neutral",
            description=it.get("description"))
        for sname in it["suppliers"]:
            if _key(sname) in sups:
                edges.append({"from": "s:" + _key(sname), "to": "i:" + sku})
        for site in it["sites"]:
            sites.add(site)
            edges.append({"from": "i:" + sku, "to": "l:" + site})
        for c in it["customers"]:
            custs.add(c)
            edges.append({"from": ("l:" + it["sites"][0]) if it["sites"] else "i:" + sku, "to": "c:" + c})
    for site in sorted(sites):
        add("l:" + site, "site", site, "Sites")
    for c in sorted(custs):
        add("c:" + c, "customer", c, "Demand")
    seen, uniq = set(), []
    for e in edges:
        t = (e["from"], e["to"])
        if t not in seen:
            seen.add(t)
            uniq.append(e)
    cols = [c for c in ["Sub-tier", "Tier 1", "Items", "Sites", "Demand"] if any(n["column"] == c for n in nodes)]
    return {"columns": cols, "nodes": nodes, "edges": uniq,
            "spofs": [n["label"] for n in nodes if n.get("spof")]}


def coverage(buffer_days: float = 7.0) -> dict:
    m = build()
    rows = []
    for it in m["items"].values():
        sup_lead = [m["suppliers"][_key(s)]["lead_time_days"] for s in it["suppliers"]
                    if _key(s) in m["suppliers"] and m["suppliers"][_key(s)]["lead_time_days"] is not None]
        lead = max(sup_lead) if sup_lead else None
        d = it["daily_demand"]
        suggested = round(d * (lead + buffer_days)) if (d and lead is not None) else None
        rows.append({**{k: it.get(k) for k in ("sku", "description", "on_hand", "reorder_point", "daily_demand", "demand_basis",
                                                "days_cover", "suppliers", "sole_sourced", "open_po_qty", "next_receipt",
                                                "stockout_date", "sites", "below_reorder")},
                     "lead_time_days": lead, "suggested_reorder_point": suggested,
                     "gap": (suggested - (it["reorder_point"] or 0)) if suggested is not None else None})
    rows.sort(key=lambda r: (r["days_cover"] is None, r["days_cover"] if r["days_cover"] is not None else 0))
    return {"buffer_days": buffer_days, "items": rows,
            "summary": {"under_7": sum(1 for r in rows if r["days_cover"] is not None and r["days_cover"] < 7),
                        "under_14": sum(1 for r in rows if r["days_cover"] is not None and r["days_cover"] < 14),
                        "below_reorder": sum(1 for r in rows if r["below_reorder"]),
                        "sole_sourced": sum(1 for r in rows if r["sole_sourced"]),
                        "unknown_demand": sum(1 for r in rows if r["daily_demand"] is None)}}


def delivery() -> dict:
    pos = _gold("purchase_order")
    today = _today()
    months = defaultdict(lambda: [0, 0])
    sup_months = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    late = []
    for p in pos:
        dd, pr = _naive(p.get("delivered_date")), _naive(p.get("promised_date"))
        if dd and pr:
            key = dd.strftime("%Y-%m")
            ok = dd <= pr
            months[key][0] += ok
            months[key][1] += 1
            sup_months[p.get("supplier")][key][0] += ok
            sup_months[p.get("supplier")][key][1] += 1
            if not ok:
                late.append({"order_id": p.get("order_id"), "sku": p.get("sku"), "supplier": p.get("supplier"),
                             "days_late": (dd - pr).days, "open": False, "promised": pr})
        elif pr and not dd and pr < today:
            late.append({"order_id": p.get("order_id"), "sku": p.get("sku"), "supplier": p.get("supplier"),
                         "days_late": (today - pr).days, "open": True, "promised": pr})
    trend = [{"month": k, "on_time": round(v[0] / v[1], 3), "lines": v[1]} for k, v in sorted(months.items())]
    late.sort(key=lambda x: (not x["open"], -x["days_late"]))
    return {"trend": trend, "late": late[:200],
            "by_supplier": {s: [{"month": k, "on_time": round(v[0] / v[1], 3), "lines": v[1]} for k, v in sorted(ms.items())]
                            for s, ms in sup_months.items() if s}}


# ---------------------------------------------------------------- mitigation plans

def plans(supplier: Optional[str] = None) -> list[dict]:
    q = {"supplier": supplier} if supplier else {}
    out = []
    for p in get_db().mitigation_plans.find(q).sort("created_at", -1):
        p["id"] = str(p.pop("_id"))
        out.append(p)
    return out


def create_plan(body: dict, user: str) -> dict:
    from . import governance
    if not (user or "").strip():
        raise ValueError("Say who is creating the plan")
    if body.get("strategy") not in STRATEGIES:
        raise ValueError(f"Strategy must be one of {', '.join(STRATEGIES)}")
    if not (body.get("supplier") and body.get("action")):
        raise ValueError("Supplier and action are required")
    s = None
    try:
        s = supplier(body["supplier"])
    except KeyError:
        pass
    doc = {"supplier": body["supplier"], "strategy": body["strategy"], "action": body["action"].strip(),
           "owner": (body.get("owner") or "").strip() or None, "due": body.get("due") or None,
           "status": "Proposed", "rating_at_creation": s["rating"] if s else None,
           "target_rating": body.get("target_rating"), "created_by": user.strip(), "created_at": now(),
           "history": [{"at": now(), "user": user.strip(), "status": "Proposed"}]}
    res = get_db().mitigation_plans.insert_one(doc)
    governance.audit("mitigation_plan_create", user.strip(), {"supplier": doc["supplier"], "strategy": doc["strategy"],
                                                              "action": doc["action"]})
    doc["id"] = str(res.inserted_id)
    doc.pop("_id", None)
    return doc


def update_plan(pid: str, status: str, user: str, note: str = "") -> dict:
    from bson import ObjectId
    from . import governance
    if status not in PLAN_STATUSES:
        raise ValueError(f"Status must be one of {', '.join(PLAN_STATUSES)}")
    if not (user or "").strip():
        raise ValueError("Say who is updating the plan")
    db = get_db()
    p = db.mitigation_plans.find_one({"_id": ObjectId(pid)})
    if not p:
        raise KeyError(pid)
    db.mitigation_plans.update_one({"_id": p["_id"]}, {"$set": {"status": status},
                                                       "$push": {"history": {"at": now(), "user": user.strip(), "status": status, "note": note}}})
    governance.audit("mitigation_plan_update", user.strip(), {"plan": pid, "status": status, "note": note})
    return {"id": pid, "status": status}
