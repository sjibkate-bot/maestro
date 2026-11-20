"""Ingestion batches, Mapping Studio decisions, profiles, drift, publish runs, and the
Risk Center data in supply risk (what-if, scenarios, decisions)."""
import os
from pathlib import Path

os.environ["MONGO_URI"] = "mongomock://"
os.environ["AI_ENABLED"] = "false"
os.environ["RISK_FEEDS_ENABLED"] = "false"

import pytest
from fastapi.testclient import TestClient

from app.ai import provider as provider_mod
from app.db import get_db
from app.main import app

c = TestClient(app)
RC = Path(__file__).resolve().parents[1] / "samples" / "risk_center"


@pytest.fixture(autouse=True)
def clean():
    db = get_db()
    for name in list(db.list_collection_names()):
        db[name].delete_many({})
    provider_mod.get_provider.cache_clear()


def upload(paths, mode="review", user="tester"):
    files = [("files", (p.name, p.read_bytes())) for p in paths]
    r = c.post("/api/batches", files=files, data={"user": user, "mode": mode})
    assert r.status_code == 200, r.text
    return r.json()


def objs(b):
    return {o["name"][:2]: o for o in b["objects"]}


def act(oid, col, action, **kw):
    return c.patch(f"/api/objects/{oid}/fields", json={"column": col, "action": action, "user": "tester", **kw})


def resolve_all(b):
    o = objs(b)
    for k, col, fld in [("10", "purchase_order", "order_id"), ("05", "risk_title", "title"), ("11", "product_or_material", "sku")]:
        assert act(o[k]["_id"], col, "ACCEPTED", canonical_field=fld).status_code == 200


def test_multi_file_batch_classifies_and_holds_unresolved():
    b = upload(sorted(RC.glob("*.csv")))
    assert len(b["objects"]) == 11 and b["rows"] == 168
    ents = {k: o["entity"] for k, o in objs(b).items()}
    assert ents == {"01": "supplier", "02": "supply_link", "03": "country_risk", "04": "transport_lane", "05": "risk_item",
                    "06": "alert", "07": "scenario", "08": "data_source", "09": "inventory", "10": "purchase_order", "11": "forecast"}
    held = {k for k, o in objs(b).items() if o["status"] == "MAPPING_REQUIRED"}
    assert held == {"05", "10", "11"}
    assert "Required field(s) not mapped: order_id" in objs(b)["10"]["gate"][0]
    assert get_db().records.count_documents({}) == 0          # nothing published before review


def test_field_actions_persist_and_shape_the_record():
    b = upload(sorted(RC.glob("*.csv")))
    o7 = objs(b)["07"]["_id"]
    r = act(o7, "scenario_id", "ACCEPTED", canonical_field="sku")
    assert r.status_code == 422 and "not a field of scenario" in r.json()["detail"]
    assert act(o7, "cost_uplift_pct", "IGNORED").status_code == 200
    assert act(o7, "annual_probability_pct", "EXTENSION", extension_key="customer risk attribute").status_code == 200
    assert act(o7, "scenario_name", "CONFIRMED", canonical_field="scenario_name").json()["fields"][1]["status"] == "ACCEPTED"
    assert c.patch(f"/api/objects/{o7}/fields", json={"column": "description", "action": "IGNORED"}).status_code == 422  # who?
    f = {x["name"]: x for x in c.get(f"/api/objects/{o7}").json()["fields"]}           # reopen
    assert f["cost_uplift_pct"]["status"] == "IGNORED" and f["annual_probability_pct"]["extension_key"] == "customer_risk_attribute"
    resolve_all(b)
    run = c.post(f"/api/batches/{b['_id']}/publish", json={"user": "tester"}).json()
    assert run["status"] == "COMPLETED" and run["records"] == 168
    assert [s["stage"] for s in run["stages"]] == ["VALIDATE", "GOVERN", "CERTIFY", "PUBLISH", "RISK"]
    assert run["stages"][2]["status"] == "PENDING_OWNER"         # certification stays a person's decision
    rec = get_db().records.find_one({"record_type": "scenario", "scenario_id": "g-tw"})
    assert "cost_uplift_pct" not in rec and "cost_uplift_pct" not in rec["attributes"]
    assert rec["attributes"] == {"customer_risk_attribute": 8.0} and rec["trust"]["grade"] == "A"
    assert get_db().publish_runs.count_documents({}) == 1


def test_publish_holds_incomplete_objects_and_records_partial_run():
    b = upload(sorted(RC.glob("*.csv")))
    run = c.post(f"/api/batches/{b['_id']}/publish", json={"user": "tester"}).json()
    assert run["status"] == "PARTIAL" and len(run["stages"][0]["held"]) == 3
    assert get_db().records.count_documents({"record_type": "purchase_order"}) == 0
    assert c.post(f"/api/batches/{b['_id']}/publish", json={}).status_code == 400   # who?


def test_profile_reuse_and_drift():
    f07 = RC / "07_geopolitical_scenarios.csv"
    b = upload([f07])
    oid = b["objects"][0]["_id"]
    act(oid, "cost_uplift_pct", "IGNORED")
    prof = c.post(f"/api/objects/{oid}/profile", json={"user": "tester"}).json()["profile"]
    assert prof["version"] == 1 and prof["status"] == "ACTIVE"
    again = upload([f07])["objects"][0]                         # same columns: mapped by profile, published
    assert again["status"] == "PUBLISHED" and again["classification"]["profile_reuse"]
    tmp = Path(os.environ.get("TMPDIR", "/tmp")) / "07_geopolitical_scenarios_2026-12-01.csv"
    tmp.write_text(f07.read_text(encoding="utf-8-sig").replace("annual_probability_pct", "annual_probability"))
    d = upload([tmp])["objects"][0]
    assert d["status"] == "DRIFT" and d["drift"]["added"] == ["annual_probability"]
    view = c.get(f"/api/objects/{d['_id']}").json()
    assert {f["name"]: f["status"] for f in view["fields"]}["cost_uplift_pct"] == "IGNORED"    # earlier decision kept
    assert c.post(f"/api/objects/{d['_id']}/profile", json={"user": "tester"}).json()["status"] == "READY"
    vs = {(p["version"], p["status"]) for p in c.get("/api/profiles").json()}
    assert vs == {(1, "SUPERSEDED"), (2, "ACTIVE")}


def test_auto_mode_and_old_single_file_endpoint_still_publish():
    b = upload([RC / "03_country_risk.csv"], mode="auto")
    assert b["objects"][0]["status"] == "PUBLISHED"
    r = c.post("/api/ingest/file", files={"file": ("04_transport_lanes.csv", (RC / "04_transport_lanes.csv").read_bytes())})
    assert r.status_code == 200 and get_db().records.count_documents({"record_type": "transport_lane"}) == 12


def test_supply_risk_uses_risk_center_data():
    b = upload(sorted(RC.glob("*.csv")))
    resolve_all(b)
    c.post(f"/api/batches/{b['_id']}/publish", json={"user": "tester"})
    sups = {s["supplier"]: s for s in c.get("/api/supply/suppliers").json()}
    assert len(sups) == 25                                        # vendor codes on POs resolve to the master
    t = sups["Taipei Semicon Assembly"]
    assert t["rating_class"]["label"] in ("High", "Critical") and t["register"] and t["factors"]["recovery_gap"] == 100
    scs = {s["scenario_id"]: s for s in c.get("/api/supply/scenarios").json()}
    assert {x["supplier"] for x in scs["g-tw"]["scope"]["suppliers"]} == {"Taipei Semicon Assembly", "Hsinchu Wafer Fab 12"}
    assert {x["supplier"] for x in scs["g-min"]["scope"]["suppliers"]} == {"Northern Rare Earth Refining", "Yunnan Gallium Works"}
    w = c.post("/api/supply/whatif", json={"scenario_id": "g-tw"}).json()
    assert w["result"]["shortage_qty"] > w["baseline"]["shortage_qty"] and w["inputs"]["horizon_days"] == 60
    d = c.post("/api/supply/whatif", json={"delay_days": 30}).json()
    assert d["result"]["short_items"] > d["baseline"]["short_items"]
    assert c.post("/api/supply/decisions", json={"decision": "OVERRIDE", "user": "t"}).status_code == 422
    assert c.post("/api/supply/decisions", json={"decision": "ACCEPT", "user": "t", "subject": "g-tw"}).status_code == 200
    assert c.get("/api/supply/decisions").json()[0]["decision"] == "ACCEPT"


def test_delete_all_clears_staging():
    upload(sorted(RC.glob("*.csv"))[:2])
    r = c.post("/api/data/delete-all", json={"confirm": "DELETE ALL", "user": "tester"})
    assert r.status_code == 200
    assert get_db().source_objects.count_documents({}) == 0 and get_db().staged_rows.count_documents({}) == 0


def test_governance_shows_tables_waiting_to_be_published():
    b = upload(sorted(RC.glob("*.csv")))
    ov = c.get("/api/governance/overview").json()
    w = ov["awaiting_publish"]
    assert w["objects"] == 11 and w["rows"] == 168 and w["ready"] == 8 and w["needs_mapping"] == 3
    assert w["batches"][0]["batch_id"] == b["_id"]
    assert ov["layers"]["bronze"]["awaiting_rows"] == 168 and ov["layers"]["bronze"]["records"] >= 168
    inv = next(p for p in ov["products"] if p["id"] == "inventory_position")
    assert inv["records"] == 0 and inv["awaiting_rows"] == 18 + 18 and inv["freshness"] == "NO DATA"   # inventory + forecast
    resolve_all(b)
    c.post(f"/api/batches/{b['_id']}/publish", json={"user": "tester"})
    assert c.get("/api/governance/overview").json()["awaiting_publish"]["objects"] == 0


def test_template_sheets_carry_no_mapping_reasons_and_fleet_status_keeps_as_of():
    demo = Path(__file__).resolve().parents[1] / "samples" / "demo_pack" / "out"
    if not (demo / "02_B-52_Fixer_2030-09-16.xlsx").exists():
        pytest.skip("demo pack not built")
    b = upload([demo / "02_B-52_Fixer_2030-09-16.xlsx", demo / "03_Fleet_Health_2030-09-16.xlsx"])
    direct = [o for o in b["objects"] if o.get("direct")]
    assert direct and all(o["gate"] == [] for o in direct)
    fs = next(o for o in b["objects"] if o.get("sheet") == "Fleet Status")
    f = {x["name"]: x for x in c.get(f"/api/objects/{fs['_id']}").json()["fields"]}
    assert f["As_Of"]["canonical_field"] == "as_of" and f["As_Of"]["status"] == "AUTO"


def test_files_can_be_added_one_by_one_to_a_batch():
    paths = sorted(RC.glob("*.csv"))
    b = upload(paths[:1])
    for p in paths[1:4]:
        r = c.post(f"/api/batches/{b['_id']}/files", files=[("files", (p.name, p.read_bytes()))])
        assert r.status_code == 200 and r.json()["load"][0]["file"] == p.name
    v = c.get(f"/api/batches/{b['_id']}").json()
    assert len(v["objects"]) == 4 and [f["file"] for f in v["files"]] == [p.name for p in paths[:4]]
    assert c.post("/api/batches/nope/files", files=[("files", ("x.csv", b"a\n1\n"))]).status_code == 404
