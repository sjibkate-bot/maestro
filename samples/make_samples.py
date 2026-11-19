"""Generate demo data for every connector type: Excel, CSV, JSON (API/ERP), IoT, PDF."""
import json
import random
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

OUT = Path(__file__).parent
random.seed(7)
SKUS = [f"SKU-{i:04d}" for i in range(1, 31)]
SUPPLIERS = ["Acme Components", "Globex Metals", "Initech Plastics", "Umbrella Packaging", "Stark Electronics"]
SITES = ["DC-Chicago", "DC-Dallas", "Plant-Pune"]
today = date.today()

# Excel: inventory with friendly headers + a second sheet with SAP-style technical names
inv = [{"Item Code": s, "Description": f"Part {s[-4:]}", "Warehouse": random.choice(SITES),
        "Qty On Hand": random.randint(0, 900), "Reorder Point": random.choice([100, 150, 250]),
        "Unit Cost": round(random.uniform(1.5, 80), 2), "UoM": "EA"} for s in SKUS]
sap = [{"MATNR": s, "WERKS": "1000", "LGORT": "0001", "LABST": random.randint(0, 400),
        "EISBE": 120, "MEINS": "PC"} for s in SKUS[:10]]
with pd.ExcelWriter(OUT / "inventory.xlsx") as xw:
    pd.DataFrame(inv).to_excel(xw, sheet_name="Stock", index=False)
    pd.DataFrame(sap).to_excel(xw, sheet_name="SAP_MARD", index=False)

# CSV: purchase order lines
po = []
for n in range(1, 61):
    ordered = today - timedelta(days=random.randint(10, 90))
    promised = ordered + timedelta(days=random.randint(7, 30))
    sup = random.choice(SUPPLIERS)
    slip = random.choice([-3, 0, 0, 1, 5, 12]) if sup != "Globex Metals" else random.choice([4, 9, 15])
    delivered = promised + timedelta(days=slip)
    po.append({"PO Number": f"PO-{4500 + n}", "Vendor": sup, "Material": random.choice(SKUS),
               "Order Qty": random.randint(50, 500), "Net Price": round(random.uniform(2, 60), 2),
               "PO Date": ordered.isoformat(), "Delivery Date": promised.isoformat(),
               "Received On": delivered.isoformat() if delivered <= today else ""})
pd.DataFrame(po).to_csv(OUT / "purchase_orders.csv", index=False)

# JSON: shipments as an OData-style API payload
ships = {"value": [{"ShipmentId": f"SHP-{900 + i}", "Carrier": random.choice(["DHL", "Maersk", "FedEx"]),
                    "ShipFrom": random.choice(["Shenzhen", "Rotterdam", "Monterrey"]),
                    "ShipTo": random.choice(SITES), "Status": random.choice(["In Transit", "Delivered", "Customs Hold"]),
                    "ETA": (today + timedelta(days=random.randint(-10, 10))).isoformat()} for i in range(25)]}
(OUT / "shipments_api.json").write_text(json.dumps(ships, indent=1))

# IoT: cold-chain temperature readings with a few excursions
now = datetime.now(timezone.utc)
readings = []
for dev in ["reefer-01", "reefer-02"]:
    for m in range(120):
        t = round(random.gauss(4.0, 0.4), 2)
        if dev == "reefer-02" and m in (60, 61):
            t = 14.5
        readings.append({"deviceId": dev, "metric": "temperature_c", "reading": t,
                         "ts": (now - timedelta(minutes=5 * (120 - m))).isoformat(), "site": "DC-Dallas"})
(OUT / "iot_readings.json").write_text(json.dumps(readings, indent=1))

# CSV: supplier master with sub-tiers (who supplies whom), country, commodity, sourcing and spend
master = [
    ("Acme Components", 1, "", "United States", "Controller PCBAs", "no", 2_400_000, 18),
    ("Globex Metals", 1, "", "Germany", "Machined housings", "yes", 3_100_000, 25),
    ("Initech Plastics", 1, "", "Mexico", "Injection-moulded parts", "no", 1_900_000, 21),
    ("Umbrella Packaging", 1, "", "Canada", "Packaging", "no", 900_000, 14),
    ("Stark Electronics", 1, "", "Taiwan", "Power electronics", "yes", 4_200_000, 30),
    ("Wayne Foundry", 2, "Globex Metals", "Poland", "Aluminium castings", "yes", 0, 35),
    ("Cyberdyne Semis", 2, "Stark Electronics", "Malaysia", "GaN power FETs", "yes", 0, 60),
    ("Tyrell Resins", 2, "Initech Plastics", "United States", "Polymer resin", "no", 0, 20),
]
pd.DataFrame(master, columns=["Supplier", "Tier", "Supplies To", "Country", "Commodity", "Sole Source",
                              "Annual Spend", "Lead Time"]).to_csv(OUT / "supplier_master.csv", index=False)

# CSV: customer orders (demand), used for days-of-cover
so = []
for n in range(1, 91):
    d = today - timedelta(days=random.randint(1, 60))
    so.append({"SO Number": f"SO-{7000 + n}", "Customer": random.choice(["North America OEM", "EU Distribution", "Aftermarket"]),
               "Item": random.choice(SKUS), "Qty": random.randint(5, 60), "Order Date": d.isoformat(),
               "Requested Date": (d + timedelta(days=10)).isoformat()})
pd.DataFrame(so).to_csv(OUT / "sales_orders.csv", index=False)

# Text: a supplier email (unstructured -> needs AI extraction)
(OUT / "supplier_email.txt").write_text(f"""From: orders@globexmetals.example
Subject: Delay notice for PO-4512 and PO-4530

Hello,
Due to a furnace outage, PO-4512 (SKU-0007, 300 EA) will ship on {(today + timedelta(days=9)).isoformat()} instead of
{(today - timedelta(days=2)).isoformat()}. PO-4530 (SKU-0019, 120 EA at USD 14.20) is unaffected and ships tomorrow.
Regards, Globex Metals
""")
print("Samples written to", OUT)

# PDF: a purchase order document (reportlab is optional)
try:
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet

    st = getSampleStyleSheet()
    rows = [["Item", "Description", "Qty", "Unit Price", "Amount"],
            ["SKU-0003", "Bracket, steel", "200", "4.10", "820.00"],
            ["SKU-0011", "Housing, ABS", "150", "12.50", "1875.00"],
            ["SKU-0024", "Cable assembly", "400", "3.25", "1300.00"]]
    doc = SimpleDocTemplate(str(OUT / "purchase_order.pdf"), pagesize=A4)
    doc.build([Paragraph("PURCHASE ORDER PO-4601", st["Title"]),
               Paragraph(f"Supplier: Initech Plastics &nbsp; Order date: {today.isoformat()} &nbsp; "
                         f"Requested delivery: {(today + timedelta(days=21)).isoformat()} &nbsp; Ship to: DC-Chicago", st["Normal"]),
               Table(rows, style=TableStyle([("GRID", (0, 0), (-1, -1), 0.5, "#888888")])), Paragraph("Currency: USD. Total 3,995.00", st["Normal"])])
except ImportError:
    print("reportlab not installed; skipping purchase_order.pdf")
