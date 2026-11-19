"""Supply analysis: ratings, explanation, network, coverage, delivery, plans."""
import os
from pathlib import Path

os.environ["MONGO_URI"] = "mongomock://"
os.environ["AI_ENABLED"] = "false"

import pytest
from fastapi.testclient import TestClient

from app.ai import provider as provider_mod
from app.db import get_db
from app.main import app

S = Path(__file__).resolve().parent.parent / "samples"
client = TestClient(app)


@pytest.fixture(autouse=True)
def loaded():
    db = get_db()
    for c in ("records", "raw", "sources", "mappings", "quarantine", "certifications", "decisions", "audit",
              "drift", "bronze_files", "governance_config", "mitigation_plans"):
        db[c].delete_many({})
    provider_mod.get_provider.cache_clear()
    yield


def load(*names):
    for n in names:
        r = client.post("/api/ingest/file", files={"file": (n, (S / n).read_bytes())})
        assert r.status_code == 200, r.text


def test_empty_state():
    o = client.get("/api/supply/overview").json()
    assert o["has_data"] is False and o["kpis"]["suppliers"] == 0


def test_ratings_and_explanation():
    load("inventory.xlsx", "purchase_orders.csv", "supplier_master.csv", "sales_orders.csv")
    sups = {s["supplier"]: s for s in client.get("/api/supply/suppliers").json()}
    assert len(sups) == 8
    for s in sups.values():
        assert s["rating"] == s["likelihood"] * s["impact"] and 1 <= s["likelihood"] <= 5 and 1 <= s["impact"] <= 5
        assert [e["step"] for e in s["explain"]] == ["What it supplies", "Where it is", "Performance", "Continuity"]
    g = sups["Globex Metals"]                       # always late in the samples, sole source of several items
    assert g["on_time_rate"] < 0.5 and g["sole_items"] and g["rating_class"]["label"] in ("High", "Critical")
    sub = sups["Cyberdyne Semis"]                   # sub-tier: no orders, inherits impact of Stark Electronics
    assert sub["tier"] == 2 and sub["inherits_from"] == "Stark Electronics"
    assert sub["impact_score"] >= sups["Stark Electronics"]["impact_score"] and sub["confidence"] < 1
    assert any("second source" in a for a in g["actions"])


def test_overview_kpis():
    load("inventory.xlsx", "purchase_orders.csv", "supplier_master.csv", "sales_orders.csv")
    k = client.get("/api/supply/overview").json()["kpis"]
    assert k["suppliers"] == 8 and k["spend"] == 12_500_000 and 0 <= k["risk_index"] <= 100
    assert k["spofs"] >= 2 and k["data_confidence"] is not None


def test_network_has_tiers_and_paths():
    load("inventory.xlsx", "purchase_orders.csv", "supplier_master.csv", "sales_orders.csv")
    n = client.get("/api/supply/network").json()
    assert n["columns"] == ["Sub-tier", "Tier 1", "Items", "Sites", "Demand"]
    assert {"from": "s:wayne foundry", "to": "s:globex metals"} in n["edges"]
    assert any(e["to"].startswith("c:") for e in n["edges"])


def test_coverage_and_what_if():
    load("inventory.xlsx", "purchase_orders.csv", "supplier_master.csv", "sales_orders.csv")
    a = client.get("/api/supply/coverage", params={"buffer_days": 0}).json()
    b = client.get("/api/supply/coverage", params={"buffer_days": 30}).json()
    ia = {i["sku"]: i for i in a["items"]}
    ib = {i["sku"]: i for i in b["items"]}
    sku = next(k for k, v in ia.items() if v["suggested_reorder_point"])
    assert ib[sku]["suggested_reorder_point"] > ia[sku]["suggested_reorder_point"]
    covered = [i for i in a["items"] if i["days_cover"] is not None]
    assert covered and covered == sorted(covered, key=lambda i: i["days_cover"])


def test_delivery_trend():
    load("purchase_orders.csv")
    d = client.get("/api/supply/delivery").json()
    assert d["trend"] and all(0 <= t["on_time"] <= 1 for t in d["trend"]) and d["late"]


def test_supply_reads_gold_only():
    load("purchase_orders.csv", "supplier_master.csv")
    bad = b"Supplier,Tier,Country,Annual Spend\nGhost Corp,1,Nowhere,-5\n"
    client.post("/api/ingest/file", files={"file": ("bad_master.csv", bad)})
    names = [s["supplier"] for s in client.get("/api/supply/suppliers").json()]
    assert "Ghost Corp" in names   # a valid supplier row (spend sign is not a hard rule) still shows
    # a supplier row without a name is quarantined (hard key) and never appears
    client.post("/api/ingest/file", files={"file": ("nameless.csv", b"Supplier,Tier,Country\n,1,Chile\n")})
    assert all(n for n in [s["supplier"] for s in client.get("/api/supply/suppliers").json()])


def test_mitigation_plans_flow():
    load("purchase_orders.csv", "supplier_master.csv")
    r = client.post("/api/supply/plans", json={"supplier": "Globex Metals", "strategy": "Treat",
                                               "action": "Qualify second source", "owner": "Buyer", "user": "Tester"})
    assert r.status_code == 200 and r.json()["status"] == "Proposed" and r.json()["rating_at_creation"]
    pid = r.json()["id"]
    assert client.post("/api/supply/plans", json={"supplier": "X", "strategy": "Ignore", "action": "a", "user": "T"}).status_code == 400
    assert client.post(f"/api/supply/plans/{pid}/status", json={"status": "Approved", "user": "Lead"}).json()["status"] == "Approved"
    plans = client.get("/api/supply/plans", params={"supplier": "Globex Metals"}).json()
    assert plans[0]["status"] == "Approved" and len(plans[0]["history"]) == 2
    assert get_db().audit.count_documents({"action": {"$in": ["mitigation_plan_create", "mitigation_plan_update"]}}) == 2
    assert client.get("/api/supply/supplier", params={"name": "Globex Metals"}).json()["plans"][0]["id"] == pid


def test_root_goes_to_supply():
    r = client.get("/", follow_redirects=False)
    assert r.status_code in (302, 307) and r.headers["location"] == "/supply"
    assert client.get("/supply").status_code == 200
