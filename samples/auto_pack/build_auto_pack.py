"""Build the car-manufacturing demo pack: a fictional premium carmaker ("Auremont Automotive",
styled on a German premium OEM; no real company data) with plants in Germany, the US and
Mexico and a supplier network across 17 countries, from tier 1 down to raw materials.

    python samples/auto_pack/build_auto_pack.py

Dates are relative to the day you build, so rebuild the day before a demo.
Everything here is synthetic. Supplier names are invented; cities, states and countries are real.
"""
import csv
import random
from datetime import date, timedelta
from pathlib import Path

import openpyxl

OUT = Path(__file__).resolve().parent / "out"
TODAY = date.today()
random.seed(42)

PLANTS = {  # code: (name, city, state, country, currency)
    "PLT-HAL": ("Plant Halle", "Halle (Saale)", "Saxony-Anhalt", "DE", "EUR"),
    "PLT-GRV": ("Plant Greenville", "Greenville", "South Carolina", "US", "USD"),
    "PLT-SLP": ("Plant San Luis Potosí", "San Luis Potosí", "San Luis Potosí", "MX", "USD"),
}

# id, name, city, state/province, country, tier, supplies_to, commodity, sole, spend (EUR m), lead days,
# buffer days, recovery days, critical, on-time rate, lane to the plants
SUPPLIERS = [
    ("HBC", "Hanseong Battery Cells", "Cheongju", "Chungcheongbuk-do", "KR", 1, None, "Battery modules", "yes", 420, 45, 10, 120, "yes", .86, "suez-red-sea"),
    ("HSI", "Hsinchu Microcontrollers", "Hsinchu", "Hsinchu City", "TW", 1, None, "Gateway & body MCUs", "yes", 95, 70, 7, 150, "yes", .72, "taiwan-strait"),
    ("PSA", "Penang Semicon Assembly", "Bayan Lepas", "Penang", "MY", 1, None, "SiC power modules", "yes", 140, 56, 10, 90, "yes", .90, "malacca"),
    ("KSW", "Kyushu Sensor Works", "Kumamoto", "Kumamoto", "JP", 1, None, "ADAS radar sensors", "no", 88, 49, 14, 60, "no", .95, "pacific"),
    ("GEM", "Győr Electric Motors", "Győr", "Győr-Moson-Sopron", "HU", 1, None, "E-motors", "no", 260, 21, 10, 45, "yes", .94, "eu-road"),
    ("JXM", "Jiangxi Rare Earth Magnetics", "Ganzhou", "Jiangxi", "CN", 1, None, "NdFeB rotor magnets", "yes", 60, 63, 20, 180, "yes", .80, "eurasia-rail"),
    ("BWH", "Bajío Wire Harness", "León", "Guanajuato", "MX", 1, None, "Main wiring harness", "yes", 150, 14, 5, 60, "yes", .70, "us-mx"),
    ("MOC", "Monterrey Castings", "Monterrey", "Nuevo León", "MX", 1, None, "Aluminium subframes", "no", 120, 18, 10, 40, "no", .91, "us-mx"),
    ("CAX", "Carolina Axle Corp", "Spartanburg", "South Carolina", "US", 1, None, "Rear axle assemblies", "no", 175, 10, 7, 30, "no", .96, "domestic"),
    ("GLG", "Great Lakes Glass", "Toledo", "Ohio", "US", 1, None, "Windscreens", "no", 55, 14, 10, 30, "no", .97, "domestic"),
    ("TGW", "Thüringen Glaswerk", "Jena", "Thuringia", "DE", 1, None, "Windscreens", "no", 48, 10, 10, 30, "no", .97, "eu-road"),
    ("ESS", "Elbtal Sitzsysteme", "Dresden", "Saxony", "DE", 1, None, "Seat sets (JIT)", "yes", 210, 3, 2, 21, "yes", .98, "eu-road"),
    ("RAT", "Rheinwerk Antriebstechnik", "Stuttgart", "Baden-Württemberg", "DE", 1, None, "8-speed transmissions", "yes", 310, 28, 12, 60, "yes", .93, "eu-road"),
    ("SBS", "Silesia Brake Systems", "Katowice", "Silesian", "PL", 1, None, "Brake calipers", "no", 70, 14, 14, 30, "no", .95, "eu-road"),
    ("BIT", "Bursa Interior Trim", "Bursa", "Bursa", "TR", 1, None, "Instrument panel trim", "no", 45, 21, 12, 45, "no", .88, "balkan-road"),
    ("CHF", "Chennai Forgings", "Chennai", "Tamil Nadu", "IN", 1, None, "Forged crankshafts", "yes", 38, 56, 14, 75, "no", .78, "suez-red-sea"),
    ("LPC", "Lombardia Paint & Coatings", "Bergamo", "Lombardy", "IT", 1, None, "Basecoat paint", "no", 33, 14, 20, 30, "no", .97, "eu-road"),
    ("RSC", "Ruhr Steel Coil", "Duisburg", "North Rhine-Westphalia", "DE", 1, None, "Galvanised steel coil", "no", 190, 21, 25, 30, "no", .96, "eu-road"),
    ("ITY", "Iberia Tyre", "Valladolid", "Castile and León", "ES", 1, None, "20-inch tyres", "no", 64, 14, 18, 30, "no", .96, "eu-road"),
    # sub-tier: no purchase orders from the OEM; reached through the supply-network links
    ("ATL", "Atacama Lithium", "Antofagasta", "Antofagasta Region", "CL", 3, "Hanseong Battery Cells", "Lithium carbonate", "no", 0, 90, 30, 120, "yes", None, "pacific"),
    ("KCR", "Katanga Cobalt Refining", "Lubumbashi", "Haut-Katanga", "CD", 3, "Hanseong Battery Cells", "Cobalt hydroxide", "yes", 0, 100, 30, 180, "yes", None, "cape-route"),
    ("GCF", "Guangdong Copper Foil", "Huizhou", "Guangdong", "CN", 2, "Hanseong Battery Cells", "Battery copper foil", "no", 0, 45, 20, 60, "no", None, "east-china-sea"),
    ("TJG", "Tianjin Graphite", "Tianjin", "Tianjin", "CN", 2, "Hanseong Battery Cells", "Anode graphite", "yes", 0, 50, 20, 120, "yes", None, "east-china-sea"),
    ("KWF", "Kaohsiung Wafer Foundry", "Kaohsiung", "Kaohsiung City", "TW", 2, "Hsinchu Microcontrollers", "40 nm wafers", "yes", 0, 90, 10, 180, "yes", None, "taiwan-strait"),
    ("VAL", "Volga Alloys", "Samara", "Samara Oblast", "RU", 2, "Silesia Brake Systems", "Nickel alloy", "no", 0, 60, 15, 90, "no", None, "eurasia-rail"),
]

# part, description, supplier id, unit price, uom, lead days, daily demand by plant, days of cover, open-order days
PARTS = [
    ("BAT-MOD-96", "Battery module 96S", "HBC", 1850.0, "EA", 45, {"PLT-HAL": 120, "PLT-GRV": 80}, 8, 30),
    ("MCU-GW-32", "Gateway MCU 32-bit", "HSI", 14.5, "EA", 70, {"PLT-HAL": 160, "PLT-GRV": 110, "PLT-SLP": 60}, 5, 18),
    ("SIC-INV-800", "SiC power module 800 V", "PSA", 410.0, "EA", 56, {"PLT-HAL": 30, "PLT-GRV": 20}, 11, 26),
    ("RAD-77G", "77 GHz radar sensor", "KSW", 96.0, "EA", 49, {"PLT-HAL": 150, "PLT-GRV": 100, "PLT-SLP": 50}, 9, 28),
    ("EMOT-150", "E-motor 150 kW", "GEM", 2300.0, "EA", 21, {"PLT-HAL": 30, "PLT-GRV": 20}, 18, 25),
    ("MAG-NDFEB", "NdFeB rotor magnet set", "JXM", 210.0, "SET", 63, {"PLT-HAL": 30}, 12, 20),
    ("HARN-MAIN", "Main wiring harness", "BWH", 640.0, "EA", 14, {"PLT-GRV": 110, "PLT-SLP": 60}, 6, 26),
    ("CAST-SUBF", "Aluminium front subframe", "MOC", 380.0, "EA", 18, {"PLT-GRV": 110, "PLT-SLP": 60}, 14, 24),
    ("AXLE-RR", "Rear axle assembly", "CAX", 1450.0, "EA", 10, {"PLT-GRV": 110}, 10, 25),
    ("GLASS-WS", "Acoustic windscreen", "GLG", 210.0, "EA", 14, {"PLT-GRV": 110, "PLT-SLP": 60}, 15, 22),
    ("GLASS-WS-EU", "Acoustic windscreen (EU)", "TGW", 195.0, "EA", 10, {"PLT-HAL": 150}, 15, 22),
    ("SEAT-FR", "Front seat set", "ESS", 1100.0, "SET", 3, {"PLT-HAL": 150}, 3, 28),
    ("TRANS-8AT", "8-speed transmission (PHEV)", "RAT", 2950.0, "EA", 28, {"PLT-HAL": 120, "PLT-SLP": 60}, 15, 24),
    ("BRAKE-CAL", "Brake caliper set", "SBS", 230.0, "SET", 14, {"PLT-HAL": 150, "PLT-GRV": 110, "PLT-SLP": 60}, 20, 22),
    ("TRIM-IP", "Instrument panel trim", "BIT", 160.0, "EA", 21, {"PLT-HAL": 150}, 12, 24),
    ("FORG-CRANK", "Forged crankshaft", "CHF", 185.0, "EA", 56, {"PLT-HAL": 120, "PLT-SLP": 60}, 16, 20),
    ("PAINT-BASE", "Basecoat paint (200 L drum)", "LPC", 1250.0, "DRUM", 14, {"PLT-HAL": 6, "PLT-GRV": 4}, 25, 15),
    ("STEEL-COIL", "Galvanised steel coil", "RSC", 1650.0, "T", 21, {"PLT-HAL": 90, "PLT-SLP": 40}, 30, 10),
    ("TYRE-20", "20-inch tyre", "ITY", 140.0, "EA", 14, {"PLT-HAL": 600, "PLT-GRV": 440}, 22, 18),
]

LANES = [("taiwan-strait", "Taiwan Strait", 74), ("suez-red-sea", "Red Sea / Suez", 82), ("malacca", "Strait of Malacca", 45),
         ("pacific", "Trans-Pacific", 30), ("east-china-sea", "East China Sea", 52), ("eurasia-rail", "China–Europe rail", 62),
         ("us-mx", "US–Mexico border", 40), ("cape-route", "Cape of Good Hope", 35), ("balkan-road", "Balkan road corridor", 28),
         ("eu-road", "EU road network", 8), ("domestic", "Domestic road / rail", 6)]

COUNTRIES = [("DE", "Germany", 12, 10), ("US", "United States", 22, 30), ("MX", "Mexico", 55, 35), ("KR", "South Korea", 30, 28),
             ("TW", "Taiwan", 74, 62), ("MY", "Malaysia", 38, 30), ("JP", "Japan", 18, 72), ("HU", "Hungary", 30, 12),
             ("CN", "China", 70, 40), ("PL", "Poland", 18, 10), ("TR", "Türkiye", 58, 45), ("IN", "India", 45, 50),
             ("IT", "Italy", 20, 25), ("ES", "Spain", 15, 15), ("CL", "Chile", 30, 55), ("CD", "DR Congo", 88, 40), ("RU", "Russia", 92, 20)]

SCENARIOS = [
    ("S-TW", "Taiwan Strait blockade", "Shipping and air cargo out of Taiwan halted for about six weeks.", 45, 1.0, 0, 6, "TW", "taiwan-strait"),
    ("S-RED", "Red Sea / Suez closure", "Attacks force Asia–Europe sailings round the Cape: +12–14 days and less capacity.", 60, 0.4, 15, 30, None, "suez-red-sea"),
    ("S-REE", "China rare-earth export controls", "Licensing on rare-earth magnets cuts volumes 70% for four months.", 120, 0.7, 20, 25, "CN", None),
    ("S-JP", "Kyushu earthquake", "M7 earthquake near Kumamoto stops sensor production for a month.", 30, 0.8, 0, 10, "JP", None),
    ("S-KR", "Battery cell plant fire, Cheongju", "Fire on two cell lines halves output for six weeks.", 45, 0.5, 0, 5, "KR", None),
    ("S-MX", "US tariff on Mexican-origin parts", "25% tariff on Mexican-origin assemblies outside USMCA rules.", 0, 0.0, 25, 25, "MX", None),
]

REGISTER = [
    ("R-01", "HSI", 4, 5, "Single source for gateway MCUs in Taiwan", "100% of gateway MCUs from one fab in Hsinchu; 5 days of cover at Halle.", "Purchasing – Electronics", "Open"),
    ("R-02", "JXM", 4, 4, "Rare-earth magnet concentration in China", "NdFeB magnets for in-house e-motors from one Ganzhou supplier; export licensing tightened.", "Purchasing – E-drive", "Open"),
    ("R-03", "HBC", 3, 5, "Battery module capacity", "Cell line yield below plan; sole source for 96S modules.", "Battery Programme", "Mitigating"),
    ("R-04", "BWH", 3, 4, "Harness plant labour unrest", "Strike vote at León; harness has 6 days of cover in Greenville.", "Purchasing – Electrical", "Open"),
    ("R-05", "CHF", 4, 3, "Red Sea routing for forgings", "Crankshafts ship Chennai → Hamburg via Suez.", "Logistics", "Open"),
    ("R-06", "KCR", 4, 3, "Cobalt sourcing compliance", "Cobalt hydroxide from Haut-Katanga; due-diligence audit overdue.", "Sustainability", "Open"),
    ("R-07", "VAL", 4, 3, "Sanctions exposure at sub-tier", "Silesia Brake Systems buys nickel alloy from a Russian mill.", "Compliance", "Open"),
    ("R-08", "KSW", 2, 4, "Seismic risk at radar supplier", "Single radar plant in Kumamoto.", "Purchasing – ADAS", "Open"),
    ("R-09", "ESS", 2, 3, "Just-in-time seats from one plant", "Seats delivered in sequence every 4 hours from Dresden.", "Plant Halle Logistics", "Open"),
]

ALERTS = [
    ("A-301", 25, "News / OSINT", "critical", "HSI", "Live-fire drills declared around Taiwan; two carriers pause bookings.", 3, "escalated"),
    ("A-302", 55, "Supplier portal", "critical", "JXM", "Export licence for October magnet shipment still pending.", 2, "new"),
    ("A-303", 90, "Carrier EDI", "high", "CHF", "Container line reroutes Asia–Europe via the Cape: +12 days on 3 bookings.", 2, "new"),
    ("A-304", 120, "News / OSINT", "high", "BWH", "Union announces strike vote at León harness plant for Friday.", 1, "new"),
    ("A-305", 240, "Supplier quality", "medium", "HBC", "Cell line 3 yield down to 88% (plan 94%).", 1, "acknowledged"),
    ("A-306", 30, "USGS feed", "medium", "KSW", "M5.9 earthquake 40 km from Kumamoto; plant inspection under way.", 1, "new"),
    ("A-307", 600, "Compliance screening", "high", "VAL", "Sub-tier mill appears on an EU restricted-party watch list (name match).", 2, "new"),
    ("A-308", 15, "Weather service", "low", "MOC", "Heavy rain warning for Monterrey; no impact expected.", 1, "new"),
]

BOM = [  # parent, child, description, qty, level
    ("AX3-EV", "SYS-BATT", "High-voltage battery", 1, 1), ("AX3-EV", "SYS-EDRIVE", "Electric drive", 1, 1),
    ("AX3-EV", "SYS-ELEC", "Electrical & electronics", 1, 1), ("AX3-EV", "SYS-CHASSIS", "Chassis", 1, 1),
    ("AX3-EV", "SYS-BODY", "Body & interior", 1, 1),
    ("SYS-BATT", "BAT-MOD-96", "Battery module 96S", 4, 2),
    ("SYS-EDRIVE", "EMOT-150", "E-motor 150 kW", 1, 2), ("SYS-EDRIVE", "SIC-INV-800", "SiC power module 800 V", 1, 2),
    ("SYS-EDRIVE", "MAG-NDFEB", "NdFeB rotor magnet set", 1, 2),
    ("SYS-ELEC", "MCU-GW-32", "Gateway MCU 32-bit", 3, 2), ("SYS-ELEC", "RAD-77G", "77 GHz radar sensor", 5, 2),
    ("SYS-ELEC", "HARN-MAIN", "Main wiring harness", 1, 2),
    ("SYS-CHASSIS", "CAST-SUBF", "Aluminium front subframe", 1, 2), ("SYS-CHASSIS", "AXLE-RR", "Rear axle assembly", 1, 2),
    ("SYS-CHASSIS", "BRAKE-CAL", "Brake caliper set", 1, 2), ("SYS-CHASSIS", "TYRE-20", "20-inch tyre", 4, 2),
    ("SYS-BODY", "STEEL-COIL", "Galvanised steel coil (t)", 0.6, 2), ("SYS-BODY", "PAINT-BASE", "Basecoat paint", 0.02, 2),
    ("SYS-BODY", "GLASS-WS-EU", "Acoustic windscreen", 1, 2), ("SYS-BODY", "SEAT-FR", "Front seat set", 1, 2),
    ("SYS-BODY", "TRIM-IP", "Instrument panel trim", 1, 2),
]


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


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    sup = {s[0]: s for s in SUPPLIERS}

    # 01 supplier master. "Site City" and "State / Province" are the two Mapping Studio decisions in the demo.
    write_csv(OUT / "01_Supplier_Master.csv", [{
        "Supplier ID": s[0], "Supplier Name": s[1], "Site City": s[2], "State / Province": s[3], "Country": s[4],
        "Tier": s[5], "Supplies To": s[6] or "", "Commodity": s[7], "Sole Source": s[8], "Annual Spend": s[9] * 1_000_000 or "",
        "Lead Time Days": s[10], "Buffer Days": s[11], "Recovery Days": s[12], "Critical": s[13]} for s in SUPPLIERS])

    write_csv(OUT / "02_Item_Master.csv", [{"Part Number": p[0], "Description": p[1], "Supplier": sup[p[2]][1], "UoM": p[4],
                                           "Lead Time Days": p[5], "Commodity": sup[p[2]][7]} for p in PARTS])

    links = []
    for s in SUPPLIERS:
        if s[5] == 1:
            plants = sorted({pl for p in PARTS if p[2] == s[0] for pl in p[6]})
            for pl in plants:
                links.append({"Upstream": s[0], "Downstream": PLANTS[pl][0], "Material": s[7], "Share Of Supply": 1.0 if s[8] == "yes" else 0.6, "Lane": s[15]})
        else:
            parent = next(x for x in SUPPLIERS if x[1] == s[6])
            links.append({"Upstream": s[0], "Downstream": parent[0], "Material": s[7], "Share Of Supply": 0.9 if s[8] == "yes" else 0.5, "Lane": s[15]})
    write_csv(OUT / "03_Supply_Network_Links.csv", links)

    inv, fc, po_rows = [], [], []
    n = 4500
    for p in PARTS:
        sku, _, sid, price, uom, lead, demand, cover, open_days = p
        for pl, d in demand.items():
            cur = PLANTS[pl][4]
            alloc = round(d * 1.5)
            inv.append({"Part Number": sku, "Plant": pl, "On Hand": round(d * cover) + alloc, "Allocated": alloc,
                        "Reorder Point": round(d * (lead / 4 + 3)), "UoM": uom, "Snapshot Date": TODAY.isoformat()})
            for wk in range(1, 9):
                fc.append({"Part Number": sku, "Plant": pl, "Need By": (TODAY + timedelta(days=7 * wk)).isoformat(),
                           "Forecast Qty": round(d * 7), "UoM": uom})
            # nine months of received orders, on-time rate per supplier
            for m in range(9, 0, -1):
                od = TODAY - timedelta(days=30 * m + 5)
                pr = od + timedelta(days=lead)
                late = random.random() > sup[sid][14]
                rd = pr + timedelta(days=random.randint(3, 15) if late else -random.randint(0, 2))
                n += 1
                po_rows.append({"PO Number": f"45{n:06d}", "Line": 10, "Vendor": sid, "Part Number": sku, "Plant": pl,
                                "Order Qty": round(d * 30), "UoM": uom, "Unit Price": price, "Currency": cur,
                                "Order Date": od.isoformat(), "Promised Date": pr.isoformat(),
                                "Received Date": rd.isoformat() if rd <= TODAY else ""})
            # open orders: a rolling schedule, one delivery every 14 days for the next 18 weeks
            for k in range(9):
                n += 1
                pr = TODAY + timedelta(days=7 + 14 * k)
                po_rows.append({"PO Number": f"45{n:06d}", "Line": 10, "Vendor": sid, "Part Number": sku, "Plant": pl,
                                "Order Qty": round(d * 14), "UoM": uom, "Unit Price": price, "Currency": cur,
                                "Order Date": (pr - timedelta(days=lead)).isoformat(), "Promised Date": pr.isoformat(),
                                "Received Date": ""})
    write_xlsx(OUT / "04_Plant_Inventory.xlsx", {"Stock": inv})
    write_csv(OUT / "05_Purchase_Orders.csv", po_rows)
    write_csv(OUT / "06_Demand_Forecast.csv", fc)
    write_csv(OUT / "07_Country_Risk.csv", [{"Country Code": c, "Country Name": nm, "Risk Index": r, "Hazard Index": h} for c, nm, r, h in COUNTRIES])
    write_csv(OUT / "08_Transport_Lanes.csv", [{"Lane Code": a, "Lane Name": b, "Lane Risk Index": c} for a, b, c in LANES])
    write_csv(OUT / "09_Risk_Register.csv", [{"Risk ID": r[0], "Affected Node": r[1], "Likelihood": r[2], "Impact": r[3], "Title": r[4],
                                              "Evidence": r[5], "Owner": r[6], "Status": r[7]} for r in REGISTER])
    write_csv(OUT / "10_Supply_Alerts.csv", [{"Alert ID": a[0], "Age Minutes": a[1], "Source System": a[2], "Severity": a[3], "Node": a[4],
                                              "Alert Text": a[5], "Level": a[6], "Status": a[7]} for a in ALERTS])
    write_csv(OUT / "11_Disruption_Scenarios.csv", [{"Scenario ID": s[0], "Scenario Name": s[1], "Description": s[2], "Duration Days": s[3],
                                                     "Supply Loss Fraction": s[4], "Cost Uplift Pct": s[5], "Annual Probability Pct": s[6],
                                                     "Country": s[7] or "", "Lane": s[8] or ""} for s in SCENARIOS])
    write_csv(OUT / "12_BOM_AX3-EV.csv", [{"End Item": "AX3-EV", "Parent": b[0], "Child": b[1], "Description": b[2], "Quantity": b[3], "Level": b[4]} for b in BOM])

    # ---- optional: manufacturing industry pack (enable it first) and an unknown file to register
    wo = []
    for i in range(30):
        q = random.choice([60, 120, 150])
        start = TODAY - timedelta(days=10 - i % 14)
        wo.append({"AUFNR": f"1000{7000 + i}", "MATNR": random.choice(["BIW-AX3", "BIW-AX5", "DOOR-FL-AX3", "HOOD-AX5"]), "GAMNG": q,
                   "IGMNG": random.randint(int(q * .4), q), "XMNGA": random.randint(0, 4), "WERKS": random.choice(["PLT-HAL", "PLT-GRV"]),
                   "ARBPL": random.choice(["BODY-L1", "BODY-L2", "PRESS-3"]), "GSTRP": start.isoformat(), "GLTRP": (start + timedelta(days=2)).isoformat(),
                   "Status": random.choice(["REL", "CNF", "TECO"])})
    write_csv(OUT / "20_Body_Shop_Work_Orders_SAP.csv", wo)
    write_csv(OUT / "21_Plant_Machines.csv", [{"Equipment": m, "Equipment Name": nm, "Work Centre": wc, "Status": st, "Uptime": up, "MTBF": mt,
                                               "Last PM": (TODAY - timedelta(days=lp)).isoformat(), "Next PM": (TODAY + timedelta(days=np)).isoformat()}
                                              for m, nm, wc, st, up, mt, lp, np in [
                                                  ("ROB-1101", "Welding robot cell 11", "BODY-L1", "Up", 97.2, 410, 20, 10),
                                                  ("ROB-1102", "Welding robot cell 12", "BODY-L1", "Maintenance", 88.5, 260, 3, 27),
                                                  ("PRS-3001", "Servo press line 3", "PRESS-3", "Up", 93.1, 380, 40, -5),
                                                  ("PNT-0501", "Paint robot booth 5", "PAINT-1", "Down", 79.0, 140, 60, -12),
                                                  ("AGV-2201", "AGV fleet marriage line", "ASSY-2", "Up", 98.4, 900, 15, 15)]])
    write_csv(OUT / "22_Quality_Inspections.csv", [{"Inspection Lot": f"89000{i:03d}", "Work Order": f"1000{7000 + i}", "Material": w["MATNR"],
                                                    "Lot Size": w["IGMNG"], "Defect Qty": d, "Usage Decision": "Pass" if d <= 1 else "Rework" if d <= 3 else "Fail",
                                                    "Defect Code": "" if d <= 1 else random.choice(["WELD-POROSITY", "PANEL-DENT", "GAP-FLUSH"]),
                                                    "Inspection Date": w["GLTRP"]}
                                                   for i, w in enumerate(wo) for d in [random.choice([0, 0, 0, 1, 2, 4])]])
    write_csv(OUT / "30_Dealer_Order_Bank.csv", [{"Dealer Code": dc, "Dealer City": city, "Model": mdl, "Orders Open": o, "Orders Confirmed": cf,
                                                  "Week": (TODAY - timedelta(days=7 * wk)).isoformat()}
                                                 for dc, city in [("D-MUC-01", "Munich"), ("D-HAM-02", "Hamburg"), ("D-ATL-11", "Atlanta"), ("D-MEX-21", "Mexico City")]
                                                 for mdl in ("AX3-EV", "AX5-PHEV") for wk in range(4)
                                                 for o in [random.randint(20, 90)] for cf in [random.randint(5, o)]])
    print(f"wrote {len(list(OUT.iterdir()))} files to {OUT}")


if __name__ == "__main__":
    main()
