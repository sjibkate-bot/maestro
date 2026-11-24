"""Shared writer for the industry demo packs (retail, pharma). Each pack supplies its sites,
suppliers, items, lanes, countries, scenarios, risk register, alerts and BOM; this writes the
twelve core files that every pack loads the same way:

    01 supplier master   02 item master       03 supply network links   04 inventory (xlsx)
    05 purchase orders   06 demand forecast   07 country risk           08 transport lanes
    09 risk register     10 supply alerts     11 disruption scenarios   12 bill of materials

Dates are relative to the build day. Everything is synthetic.
"""
import csv
import random
from datetime import date, timedelta
from pathlib import Path

import openpyxl

TODAY = date.today()


def write_csv(path: Path, rows: list[dict]):
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def write_xlsx(path: Path, sheets: dict[str, list[dict]]):
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        ws.append(list(rows[0]))
        for r in rows:
            ws.append(list(r.values()))
    wb.save(path)


def build_core(out: Path, p: dict, seed: int = 42) -> None:
    """p keys: SITES {code: (name, city, state, country, currency)}, SUPPLIERS, PARTS, LANES, COUNTRIES,
    SCENARIOS, REGISTER, ALERTS, BOM, END_ITEM, site_col, item_col, inventory_file, bom_file, po_prefix,
    peak (optional {"weeks": [...], "factor": x, "skus": {...}}: a seasonal peak in the forecast)."""
    random.seed(seed)
    out.mkdir(parents=True, exist_ok=True)
    SITES, SUPPLIERS, PARTS = p["SITES"], p["SUPPLIERS"], p["PARTS"]
    site_col, item_col = p["site_col"], p["item_col"]
    sup = {s[0]: s for s in SUPPLIERS}

    # 01: "Site City" and "State / Province" are the two Mapping Studio decisions in every pack's demo
    write_csv(out / "01_Supplier_Master.csv", [{
        "Supplier ID": s[0], "Supplier Name": s[1], "Site City": s[2], "State / Province": s[3], "Country": s[4],
        "Tier": s[5], "Supplies To": s[6] or "", "Commodity": s[7], "Sole Source": s[8], "Annual Spend": s[9] * 1_000_000 or "",
        "Lead Time Days": s[10], "Buffer Days": s[11], "Recovery Days": s[12], "Critical": s[13]} for s in SUPPLIERS])

    write_csv(out / "02_Item_Master.csv", [{item_col: x[0], "Description": x[1], "Supplier": sup[x[2]][1], "UoM": x[4],
                                           "Lead Time Days": x[5], "Commodity": sup[x[2]][7]} for x in PARTS])

    links = []
    for s in SUPPLIERS:
        if s[5] == 1:
            for site in sorted({st for x in PARTS if x[2] == s[0] for st in x[6]}):
                links.append({"Upstream": s[0], "Downstream": SITES[site][0], "Material": s[7],
                              "Share Of Supply": 1.0 if s[8] == "yes" else 0.6, "Lane": s[15]})
        else:
            parent = next(x for x in SUPPLIERS if x[1] == s[6])
            links.append({"Upstream": s[0], "Downstream": parent[0], "Material": s[7],
                          "Share Of Supply": 0.9 if s[8] == "yes" else 0.5, "Lane": s[15]})
    write_csv(out / "03_Supply_Network_Links.csv", links)

    peak = p.get("peak") or {}
    inv, fc, po_rows = [], [], []
    n = 0
    for x in PARTS:
        sku, _, sid, price, uom, lead, demand, cover, _open = x
        for site, d in demand.items():
            cur = SITES[site][4]
            alloc = round(d * 1.5)
            inv.append({item_col: sku, site_col: site, "On Hand": round(d * cover) + alloc, "Allocated": alloc,
                        "Reorder Point": round(d * (lead / 4 + 3)), "UoM": uom, "Snapshot Date": TODAY.isoformat()})
            for wk in range(1, 9):
                f = peak.get("factor", 1) if wk in peak.get("weeks", ()) and sku in peak.get("skus", ()) else 1
                fc.append({item_col: sku, site_col: site, "Need By": (TODAY + timedelta(days=7 * wk)).isoformat(),
                           "Forecast Qty": round(d * 7 * f), "UoM": uom})
            for m in range(9, 0, -1):          # nine months of received orders, on time per supplier rate
                od = TODAY - timedelta(days=30 * m + 5)
                pr = od + timedelta(days=lead)
                late = random.random() > sup[sid][14]
                rd = pr + timedelta(days=random.randint(3, 15) if late else -random.randint(0, 2))
                n += 1
                po_rows.append({"PO Number": f"{p['po_prefix']}{n:06d}", "Line": 10, "Vendor": sid, item_col: sku, site_col: site,
                                "Order Qty": round(d * 30), "UoM": uom, "Unit Price": price, "Currency": cur,
                                "Order Date": od.isoformat(), "Promised Date": pr.isoformat(),
                                "Received Date": rd.isoformat() if rd <= TODAY else ""})
            for k in range(9):                 # open orders: a delivery every 14 days for 18 weeks
                n += 1
                pr = TODAY + timedelta(days=7 + 14 * k)
                po_rows.append({"PO Number": f"{p['po_prefix']}{n:06d}", "Line": 10, "Vendor": sid, item_col: sku, site_col: site,
                                "Order Qty": round(d * 14), "UoM": uom, "Unit Price": price, "Currency": cur,
                                "Order Date": (pr - timedelta(days=lead)).isoformat(), "Promised Date": pr.isoformat(),
                                "Received Date": ""})
    write_xlsx(out / p["inventory_file"], {"Stock": inv})
    write_csv(out / "05_Purchase_Orders.csv", po_rows)
    write_csv(out / "06_Demand_Forecast.csv", fc)
    write_csv(out / "07_Country_Risk.csv", [{"Country Code": c, "Country Name": nm, "Risk Index": r, "Hazard Index": h}
                                             for c, nm, r, h in p["COUNTRIES"]])
    write_csv(out / "08_Transport_Lanes.csv", [{"Lane Code": a, "Lane Name": b, "Lane Risk Index": c} for a, b, c in p["LANES"]])
    write_csv(out / "09_Risk_Register.csv", [{"Risk ID": r[0], "Affected Node": r[1], "Likelihood": r[2], "Impact": r[3], "Title": r[4],
                                              "Evidence": r[5], "Owner": r[6], "Status": r[7]} for r in p["REGISTER"]])
    write_csv(out / "10_Supply_Alerts.csv", [{"Alert ID": a[0], "Age Minutes": a[1], "Source System": a[2], "Severity": a[3], "Node": a[4],
                                              "Alert Text": a[5], "Level": a[6], "Status": a[7]} for a in p["ALERTS"]])
    write_csv(out / "11_Disruption_Scenarios.csv", [{"Scenario ID": s[0], "Scenario Name": s[1], "Description": s[2], "Duration Days": s[3],
                                                     "Supply Loss Fraction": s[4], "Cost Uplift Pct": s[5], "Annual Probability Pct": s[6],
                                                     "Country": s[7] or "", "Lane": s[8] or ""} for s in p["SCENARIOS"]])
    write_csv(out / p["bom_file"], [{"End Item": p["END_ITEM"], "Parent": b[0], "Child": b[1], "Description": b[2], "Quantity": b[3],
                                     "Level": b[4]} for b in p["BOM"]])
