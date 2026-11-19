"""Supply-chain KPIs and risk signals computed from canonical records."""
from collections import defaultdict
from datetime import datetime, timezone
from statistics import mean, pstdev
from typing import Optional

from .db import get_db

DOC_LIMIT = 200_000


def _aware(d: Optional[datetime]) -> Optional[datetime]:
    if d is None:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _records(record_type: str, query: Optional[dict] = None):
    q = {"record_type": record_type, **(query or {})}
    return list(get_db().records.find(q, {"_id": 0, "attributes": 0}).limit(DOC_LIMIT))


def inventory_summary(query=None) -> dict:
    inv = _records("inventory", query)
    # Keep the latest snapshot per (sku, location).
    latest: dict[tuple, dict] = {}
    for r in inv:
        k = (r.get("sku"), r.get("location"))
        if k not in latest or r["ingested_at"] >= latest[k]["ingested_at"]:
            latest[k] = r
    rows = list(latest.values())
    value = sum((r.get("total_value") or (r.get("quantity") or 0) * (r.get("unit_cost") or 0)) for r in rows)
    below = [
        {"sku": r.get("sku"), "location": r.get("location"), "on_hand": r.get("quantity"),
         "reorder_point": r.get("reorder_point"),
         "shortfall": round((r.get("reorder_point") or 0) - (r.get("quantity") or 0), 2)}
        for r in rows
        if r.get("reorder_point") is not None and (r.get("quantity") or 0) <= r["reorder_point"]
    ]
    below.sort(key=lambda x: -x["shortfall"])
    return {"sku_locations": len(rows), "inventory_value": round(value, 2),
            "below_reorder_point": below, "stockouts": sum(1 for r in rows if (r.get("quantity") or 0) <= 0)}


def supplier_performance(query=None) -> dict:
    now = datetime.now(timezone.utc)
    pos = _records("purchase_order", query)
    stats = defaultdict(lambda: {"lines": 0, "delivered": 0, "on_time": 0, "late_open": 0,
                                 "lead_times": [], "spend": 0.0})
    late_lines = []
    for r in pos:
        s = stats[r.get("supplier") or "unknown"]
        s["lines"] += 1
        s["spend"] += r.get("total_value") or (r.get("quantity") or 0) * (r.get("unit_cost") or 0)
        promised, delivered, ordered = (_aware(r.get(k)) for k in ("promised_date", "delivered_date", "order_date"))
        if delivered:
            s["delivered"] += 1
            if promised is None or delivered <= promised:
                s["on_time"] += 1
            else:
                late_lines.append({"order_id": r.get("order_id"), "sku": r.get("sku"),
                                   "supplier": r.get("supplier"), "days_late": (delivered - promised).days})
            if ordered:
                s["lead_times"].append((delivered - ordered).days)
        elif promised and promised < now:
            s["late_open"] += 1
            late_lines.append({"order_id": r.get("order_id"), "sku": r.get("sku"),
                               "supplier": r.get("supplier"), "days_late": (now - promised).days,
                               "open": True})
        if r.get("lead_time_days") is not None and not delivered:
            s["lead_times"].append(r["lead_time_days"])
    suppliers = []
    for name, s in stats.items():
        suppliers.append({
            "supplier": name, "po_lines": s["lines"], "spend": round(s["spend"], 2),
            "on_time_rate": round(s["on_time"] / s["delivered"], 3) if s["delivered"] else None,
            "avg_lead_time_days": round(mean(s["lead_times"]), 1) if s["lead_times"] else None,
            "late_open_lines": s["late_open"],
        })
    suppliers.sort(key=lambda x: (x["on_time_rate"] is None, x["on_time_rate"] or 0))
    late_lines.sort(key=lambda x: -x["days_late"])
    return {"suppliers": suppliers, "late_lines": late_lines[:50], "po_lines": len(pos)}


def shipment_summary(query=None) -> dict:
    now = datetime.now(timezone.utc)
    ships = _records("shipment", query)
    delayed = []
    for r in ships:
        promised, delivered = _aware(r.get("promised_date")), _aware(r.get("delivered_date"))
        if promised and ((delivered and delivered > promised) or (not delivered and promised < now)):
            delayed.append({"shipment_id": r.get("shipment_id"), "origin": r.get("origin"),
                            "destination": r.get("destination"), "status": r.get("status"),
                            "days_late": ((delivered or now) - promised).days})
    delayed.sort(key=lambda x: -x["days_late"])
    return {"shipments": len(ships), "delayed": delayed[:50], "delayed_count": len(delayed)}


def demand_summary(query=None) -> dict:
    sos = _records("sales_order", query)
    by_sku = defaultdict(float)
    for r in sos:
        by_sku[r.get("sku") or "unknown"] += r.get("quantity") or 0
    top = sorted(by_sku.items(), key=lambda kv: -kv[1])[:20]
    return {"order_lines": len(sos), "top_skus": [{"sku": k, "quantity": v} for k, v in top]}


def sensor_anomalies(z: float = 3.0, query=None) -> dict:
    readings = _records("sensor_reading", query)
    groups = defaultdict(list)
    for r in readings:
        if r.get("value") is not None:
            groups[(r.get("device_id"), r.get("metric"))].append(r)
    anomalies = []
    for (dev, metric), rs in groups.items():
        vals = [r["value"] for r in rs]
        if len(vals) < 5:
            continue
        mu, sd = mean(vals), pstdev(vals)
        if sd == 0:
            continue
        for r in rs:
            score = (r["value"] - mu) / sd
            if abs(score) >= z:
                anomalies.append({"device_id": dev, "metric": metric, "value": r["value"],
                                  "timestamp": r.get("timestamp"), "z": round(score, 2),
                                  "location": r.get("location")})
    anomalies.sort(key=lambda a: -abs(a["z"]))
    return {"readings": len(readings), "devices": len({d for d, _ in groups}), "anomalies": anomalies[:50]}


def overview() -> dict:
    db = get_db()
    counts = {}
    for row in db.records.aggregate([{"$group": {"_id": "$record_type", "n": {"$sum": 1}}}]):
        counts[row["_id"]] = row["n"]
    inv, sup, shp = inventory_summary(), supplier_performance(), shipment_summary()
    dem, iot = demand_summary(), sensor_anomalies()
    rated = [s["on_time_rate"] for s in sup["suppliers"] if s["on_time_rate"] is not None]
    kpis = {
        "total_records": sum(counts.values()),
        "inventory_value": inv["inventory_value"],
        "skus_below_reorder_point": len(inv["below_reorder_point"]),
        "stockouts": inv["stockouts"],
        "supplier_on_time_rate": round(mean(rated), 3) if rated else None,
        "late_po_lines": len(sup["late_lines"]),
        "delayed_shipments": shp["delayed_count"],
        "sensor_anomalies": len(iot["anomalies"]),
    }
    return {"kpis": kpis, "record_counts": counts, "inventory": inv, "suppliers": sup,
            "shipments": shp, "demand": dem, "iot": iot}
