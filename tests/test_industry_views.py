"""Governance products and Maestro modules follow the industry: what is loaded and which packs are on."""
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
SAMPLES = Path(__file__).resolve().parents[1] / "samples"
AVIATION_WORDS = ("aircraft", "amxg", "weapon", "tail", "amrep", "pdm", "alc ")


@pytest.fixture(autouse=True)
def clean():
    db = get_db()
    for n in list(db.list_collection_names()):
        db[n].delete_many({})
    registry.load()
    provider_mod.get_provider.cache_clear()


def load(paths, name):
    files = [("files", (p.name, p.read_bytes())) for p in paths]
    r = c.post("/api/batches", files=files, data={"user": "T", "mode": "auto", "name": name})
    assert r.status_code == 200, r.text
    return r.json()


def load_car():
    return load(sorted(p for p in (SAMPLES / "auto_pack" / "out").iterdir() if p.suffix in (".csv", ".xlsx")), "car")


def load_b52():
    return load([SAMPLES / "demo_pack" / "out" / f for f in
                 ("02_B-52_Fixer_2030-09-16.xlsx", "03_Fleet_Health_2030-09-16.xlsx", "04_OC-ALC_Weapon_System_Summary.xlsx")], "b52")


def shown_products():
    return [p for p in c.get("/api/governance/overview").json()["products"] if p["has_data"]]


def visible_modules():
    return {k for k, m in c.get("/api/maestro/modules").json().items() if m["visible"]}


def test_nothing_loaded_shows_no_products_and_no_data_modules():
    assert shown_products() == []
    assert visible_modules() == set()


def test_car_maker_sees_no_aircraft_wording_or_modules():
    load_car()
    shown = shown_products()
    assert {p["id"] for p in shown} >= {"inventory_position", "supplier_performance", "risk_register"}
    assert not {"depot_flow", "fleet_health", "alc_summary", "production_schedule"} & {p["id"] for p in shown}
    text = " ".join(f'{p["name"]} {p["description"]} {p["owner"]}' for p in shown).lower()
    assert not [w for w in AVIATION_WORDS if w in text], text
    assert visible_modules() == set()
    # the manufacturing pack gives the products and owners manufacturing names
    assert c.post("/api/packs/manufacturing/enable", json={"user": "T"}).status_code == 200
    ps = {p["id"]: p for p in c.get("/api/governance/products").json()}
    assert ps["fleet_health"]["name"] == "Equipment Health & Reliability"
    assert ps["supplier_performance"]["owner"] == "Purchasing & Supply Chain Manager"


def test_aviation_depot_sees_its_modules_and_names():
    load_b52()
    ids = {p["id"] for p in shown_products()}
    assert {"depot_flow", "fleet_health", "alc_summary"} <= ids
    assert visible_modules() == {"amxg", "depot", "fleet"}
    ps = {p["id"]: p for p in c.get("/api/governance/products").json()}
    assert ps["depot_flow"]["name"] == "Process Flow & Gate Performance" and ps["depot_flow"]["owner"] == "Operations Manager"
    assert c.post("/api/packs/aviation_mro/enable", json={"user": "T"}).status_code == 200
    ps = {p["id"]: p for p in c.get("/api/governance/products").json()}
    assert ps["depot_flow"]["name"] == "Depot Flow & Gate Performance"
    assert ps["depot_flow"]["owner"] == "AMXG Production Superintendent"
    assert c.get("/api/governance/config").json()["domains"]["fleet"]["sensitivity"] == "CUI"
    # the gate graph's trust label uses the same name
    assert c.get("/api/maestro/gate/Post Dock").json()["trust"]["product"] == "Depot Flow & Gate Performance"


def test_mixed_customer_and_latest_pack_wins():
    load_car()
    load_b52()
    assert visible_modules() == {"amxg", "depot", "fleet"}
    assert {"depot_flow", "inventory_position", "supplier_performance"} <= {p["id"] for p in shown_products()}
    c.post("/api/packs/aviation_mro/enable", json={"user": "T"})
    c.post("/api/packs/manufacturing/enable", json={"user": "T"})
    names = {p["id"]: p["name"] for p in c.get("/api/governance/products").json()}
    assert names["depot_flow"] == "Plant Flow & Station Performance"


def test_admin_can_force_modules_on_or_off():
    r = c.put("/api/admin/modules", json={"fleet": "on", "amxg": "off"})
    assert r.status_code == 200 and r.json()["fleet"]["visible"] and not r.json()["fleet"]["has_data"]
    load_b52()
    m = c.get("/api/maestro/modules").json()
    assert m["fleet"]["visible"] and not m["amxg"]["visible"] and m["depot"]["visible"]
    assert c.put("/api/admin/modules", json={"fleet": "maybe"}).status_code == 422
    assert c.put("/api/admin/modules", json={"supply": "off"}).status_code == 422
    assert get_db().audit.find_one({"action": "module_visibility"})


def test_awaiting_rows_count_as_data():
    # loaded in review mode, the rows wait in Mapping Studio and are not yet in Silver or Gold
    files = [("files", ("01_Supplier_Master.csv", (SAMPLES / "auto_pack" / "out" / "01_Supplier_Master.csv").read_bytes()))]
    c.post("/api/batches", files=files, data={"user": "T", "mode": "review", "name": "waiting"})
    sp = next(p for p in c.get("/api/governance/overview").json()["products"] if p["id"] == "supplier_performance")
    assert sp["records"] == 0 and sp["awaiting_rows"] > 0 and sp["has_data"]
