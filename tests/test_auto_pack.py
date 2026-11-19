"""The car-manufacturing demo pack loads with exactly the planned Mapping Studio decisions and
tells the intended supply-risk story."""
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


@pytest.fixture(scope="module")
def pack(tmp_path_factory):
    spec = importlib.util.spec_from_file_location("bap", ROOT / "samples" / "auto_pack" / "build_auto_pack.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    m.OUT = tmp_path_factory.mktemp("auto")
    m.main()
    return m.OUT


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


def test_core_files_and_story(pack):
    names = sorted(p.name for p in pack.iterdir() if p.name[:2] in {f"{i:02d}" for i in range(1, 13)})
    b = upload(pack, names)
    held = [o for o in b["objects"] if o["status"] != "READY"]
    assert len(b["objects"]) == 12 and [o["file"] for o in held] == ["01_Supplier_Master.csv"]
    oid = held[0]["_id"]
    f = {x["name"]: x for x in c.get(f"/api/objects/{oid}").json()["fields"]}
    assert f["Site City"]["canonical_field"] == "location" and f["State / Province"]["canonical_field"] == "status"
    c.patch(f"/api/objects/{oid}/fields", json={"column": "Site City", "action": "ACCEPTED", "user": "SJ"})
    c.patch(f"/api/objects/{oid}/fields", json={"column": "State / Province", "action": "EXTENSION", "extension_key": "state_province", "user": "SJ"})
    run = c.post(f"/api/batches/{b['_id']}/publish", json={"user": "SJ"}).json()
    assert run["status"] == "COMPLETED" and get_db().quarantine.count_documents({}) == 0
    rec = c.get("/api/entities/supplier/records?supplier_id=BWH").json()["rows"][0]
    assert (rec["location"], rec["ext.state_province"], rec["country"]) == ("León", "Guanajuato", "MX")
    sups = {s["supplier"]: s for s in c.get("/api/supply/suppliers").json()}
    assert len(sups) == 25 and len({s["country"] for s in sups.values()}) == 17
    assert sups["Volga Alloys"]["rating_class"]["label"] == "Critical"
    assert sups["Hsinchu Microcontrollers"]["rating_class"]["label"] in ("High", "Critical")
    assert sups["Ruhr Steel Coil"]["rating_class"]["label"] == "Low"
    assert sups["Atacama Lithium"]["supplies_to"] == "Hanseong Battery Cells"
    scs = {s["scenario_id"]: {x["supplier"] for x in s["scope"]["suppliers"]} for s in c.get("/api/supply/scenarios").json()}
    assert scs["S-TW"] >= {"Hsinchu Microcontrollers"} and scs["S-REE"] == {"Jiangxi Rare Earth Magnetics"}
    assert {"Chennai Forgings", "Hanseong Battery Cells"} <= scs["S-RED"]
    tw = c.post("/api/supply/whatif", json={"scenario_id": "S-TW"}).json()
    assert any(x["sku"] == "MCU-GW-32" and x["shortage"] > x["baseline_shortage"] for x in tw["changed"])
    mx = c.post("/api/supply/whatif", json={"scenario_id": "S-MX"}).json()
    assert mx["result"]["cost_impact"]["extra_cost"] > 0
    d = c.post("/api/supply/whatif", json={"delay_days": 14}).json()
    assert set(d["result"]["value_at_risk_by_currency"]) == {"EUR", "USD"}


def test_optional_any_industry_files(pack):
    names = ["20_Body_Shop_Work_Orders_SAP.csv", "21_Plant_Machines.csv", "22_Quality_Inspections.csv", "30_Dealer_Order_Bank.csv"]
    assert {o["entity"] for o in upload(pack, names)["objects"]} == {"other"}
    c.post("/api/packs/manufacturing/enable", json={"user": "SJ"})
    b = upload(pack, names)
    ents = {o["file"]: o["entity"] for o in b["objects"]}
    assert ents["20_Body_Shop_Work_Orders_SAP.csv"] == "work_order" and ents["21_Plant_Machines.csv"] == "machine"
    assert ents["22_Quality_Inspections.csv"] == "quality_inspection" and ents["30_Dealer_Order_Bank.csv"] == "other"
    d = next(o for o in b["objects"] if o["file"] == "30_Dealer_Order_Bank.csv")
    p = c.get(f"/api/objects/{d['_id']}/propose-entity?domain=sales").json()
    assert p["name"] == "dealer_order_bank"
    p["key"] = ["dealer_code", "model", "week"]
    assert c.post(f"/api/objects/{d['_id']}/register-entity", json={"definition": p, "user": "SJ"}).json()["status"] == "READY"
    assert c.post(f"/api/batches/{b['_id']}/publish", json={"user": "SJ"}).json()["status"] == "COMPLETED"
