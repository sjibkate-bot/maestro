"""The retail (South Asian grocery importer) and pharma demo packs load with the planned Mapping
Studio decisions, tell their supply-risk stories, and show the matching industry pack at work."""
import importlib.util
import os
from pathlib import Path

os.environ["MONGO_URI"] = "mongomock://"
os.environ["AI_ENABLED"] = "false"
os.environ["RISK_FEEDS_ENABLED"] = "false"

import pytest
from fastapi.testclient import TestClient

from app import registry
from app.ai import provider as provider_mod
from app.db import get_db
from app.main import app

c = TestClient(app)
ROOT = Path(__file__).resolve().parents[1]


def build(tmp_path_factory, rel, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "samples" / rel)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    m.OUT = tmp_path_factory.mktemp(name)
    m.main()
    return m.OUT


@pytest.fixture(scope="module")
def retail(tmp_path_factory):
    return build(tmp_path_factory, "retail_pack/build_retail_pack.py", "retail")


@pytest.fixture(scope="module")
def pharma(tmp_path_factory):
    return build(tmp_path_factory, "pharma_pack/build_pharma_pack.py", "pharma")


@pytest.fixture(autouse=True)
def clean():
    db = get_db()
    for n in list(db.list_collection_names()):
        db[n].delete_many({})
    registry.load()
    provider_mod.get_provider.cache_clear()


def upload(out, names):
    r = c.post("/api/batches", files=[("files", (n, (out / n).read_bytes())) for n in names], data={"user": "SJ"})
    assert r.status_code == 200, r.text
    return r.json()


def publish_core(out):
    names = sorted(p.name for p in out.iterdir() if p.name[:2] in {f"{i:02d}" for i in range(1, 13)})
    b = upload(out, names)
    held = [o for o in b["objects"] if o["status"] != "READY"]
    assert len(b["objects"]) == 12 and [o["file"] for o in held] == ["01_Supplier_Master.csv"]
    oid = held[0]["_id"]
    c.patch(f"/api/objects/{oid}/fields", json={"column": "Site City", "action": "ACCEPTED", "user": "SJ"})
    c.patch(f"/api/objects/{oid}/fields", json={"column": "State / Province", "action": "EXTENSION", "extension_key": "state_province", "user": "SJ"})
    run = c.post(f"/api/batches/{b['_id']}/publish", json={"user": "SJ"}).json()
    assert run["status"] == "COMPLETED" and get_db().quarantine.count_documents({}) == 0
    sups = {s["supplier"]: s for s in c.get("/api/supply/suppliers").json()}
    scs = {s["scenario_id"]: {x["supplier"] for x in s["scope"]["suppliers"]} for s in c.get("/api/supply/scenarios").json()}
    return sups, scs


def worse(sid):
    w = c.post("/api/supply/whatif", json={"scenario_id": sid}).json()
    return w["result"]["short_items"] > w["baseline"]["short_items"]


def test_retail_core_story(retail):
    sups, scs = publish_core(retail)
    assert len(sups) == 25 and {s["country"] for s in sups.values()} >= {"IN", "LK", "VN", "MX"}
    assert sups["Guntur Chilli Traders"]["rating_class"]["label"] == "Critical"
    assert sups["Panhandle Flour Milling"]["rating_class"]["label"] == "Low"
    assert sups["Unjha Seed Traders"]["supplies_to"] == "Garden State Co-Packers"
    rec = c.get("/api/entities/supplier/records?supplier_id=CFV").json()["rows"][0]
    assert (rec["location"], rec["ext.state_province"], rec["country"]) == ("Celaya", "Guanajuato", "MX")
    assert scs["S-MON"] == {"Guntur Chilli Traders", "Indore Pulses & Dals", "Unjha Seed Traders"}
    assert scs["S-RICE"] == {"Karnal Basmati Mills", "Kaithal Paddy Growers"}
    assert scs["S-VN"] == {"Can Tho Rice Export", "Ca Mau Seafood", "An Giang Paddy Co-op"}
    assert scs["S-MX"] == {"Celaya Frozen Vegetables", "Sinaloa Mango Pulp", "Veracruz Lime Farms"}
    assert {"Kerala Spice Exporters", "Ceylon Coconut Products", "Karachi Halal Foods"} <= scs["S-RED"]
    assert not {s for s in scs["S-RED"] if sups[s]["country"] in ("VN", "MX", "US")}
    for sid in ("S-RED", "S-MON", "S-RICE", "S-PORT", "S-LK", "S-VN"):
        assert worse(sid), sid
    assert c.post("/api/supply/whatif", json={"scenario_id": "S-MX"}).json()["result"]["cost_impact"]["extra_cost"] > 0
    # the festival peak shows in the forecast
    fc = [r for r in c.get("/api/entities/forecast/records?sku=GHE-COW-32&limit=100").json()["rows"]]
    assert max(r["quantity"] for r in fc) > 1.5 * min(r["quantity"] for r in fc)


def test_retail_industry_pack(retail):
    names = ["20_Customer_Stores.csv", "21_Store_Sales_POS.csv", "22_Promotions.csv", "30_Import_Entries.csv"]
    assert {o["entity"] for o in upload(retail, names)["objects"]} == {"other"}
    c.post("/api/packs/retail/enable", json={"user": "SJ"})
    b = upload(retail, names)
    ents = {o["file"]: o["entity"] for o in b["objects"]}
    assert ents == {"20_Customer_Stores.csv": "store", "21_Store_Sales_POS.csv": "store_sales",
                    "22_Promotions.csv": "promotion", "30_Import_Entries.csv": "other"}
    d = next(o for o in b["objects"] if o["file"] == "30_Import_Entries.csv")
    p = c.get(f"/api/objects/{d['_id']}/propose-entity?domain=trade_compliance").json()
    assert p["name"] == "import_entries" and p["key"] == ["entry_number"]
    assert c.post(f"/api/objects/{d['_id']}/register-entity", json={"definition": p, "user": "SJ"}).json()["status"] == "READY"
    assert c.post(f"/api/batches/{b['_id']}/publish", json={"user": "SJ"}).json()["status"] == "COMPLETED"
    names = {p["id"]: p["name"] for p in c.get("/api/governance/products").json()}
    assert names["logistics"] == "Store & DC Logistics"


def test_pharma_core_story(pharma):
    sups, scs = publish_core(pharma)
    assert len(sups) == 21 and {s["country"] for s in sups.values()} >= {"IN", "CN", "IT", "DE", "KR", "PR"}
    assert sups["Vizag Fine Chemicals"]["rating_class"]["label"] in ("High", "Critical")
    assert sups["Carolina Blister Foil"]["rating_class"]["label"] == "Low"
    assert sups["Taizhou Pharma Intermediates"]["supplies_to"] == "Hyderabad API Labs"
    assert scs["S-FDA"] == {"Vizag Fine Chemicals"}
    assert scs["S-ASF"] == {"Hubei Heparin Biochem", "Chengdu Porcine Mucosa"}
    assert scs["S-VIAL"] == {"Mainz Pharma Glass", "Mitterteich Glass Tubing"}
    assert scs["S-HUR"] == {"Caguas Labels & Cartons"}
    assert {"Hyderabad API Labs", "Vizag Fine Chemicals"} <= scs["S-KSM"]
    for sid in ("S-KSM", "S-ASF", "S-VIAL", "S-FDA", "S-HUR", "S-AIR"):
        assert worse(sid), sid
    d = c.post("/api/supply/whatif", json={"delay_days": 30}).json()
    assert set(d["result"]["value_at_risk_by_currency"]) == {"USD", "EUR"}


def test_pharma_industry_pack(pharma):
    names = ["20_Batch_Lots.csv", "21_Cold_Chain_Excursions.csv", "22_Supplier_Audits.csv", "30_Serialization_Events.csv"]
    assert {o["entity"] for o in upload(pharma, names)["objects"]} == {"other"}
    c.post("/api/packs/life_sciences/enable", json={"user": "SJ"})
    b = upload(pharma, names)
    ents = {o["file"]: o["entity"] for o in b["objects"]}
    assert ents == {"20_Batch_Lots.csv": "batch_lot", "21_Cold_Chain_Excursions.csv": "temperature_excursion",
                    "22_Supplier_Audits.csv": "supplier_audit", "30_Serialization_Events.csv": "other"}
    d = next(o for o in b["objects"] if o["file"] == "30_Serialization_Events.csv")
    p = c.get(f"/api/objects/{d['_id']}/propose-entity?domain=traceability").json()
    assert p["name"] == "serialization_events"
    assert c.post(f"/api/objects/{d['_id']}/register-entity", json={"definition": p, "user": "SJ"}).json()["status"] == "READY"
    assert c.post(f"/api/batches/{b['_id']}/publish", json={"user": "SJ"}).json()["status"] == "COMPLETED"
    lots = c.get("/api/entities/batch_lot/records?limit=50").json()["rows"]
    assert len(lots) == 12 and {r["lot_status"] for r in lots} == {"Released", "Quarantined", "On hold", "Rejected"}
