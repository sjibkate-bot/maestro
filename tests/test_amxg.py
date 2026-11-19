"""AMXG modules on the demo pack: hub, events, timelines, tiers, status, icicles and gate script."""
import importlib.util
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

ROOT = Path(__file__).resolve().parents[1]
client = TestClient(app)


@pytest.fixture(scope="module")
def loaded(tmp_path_factory):
    spec = importlib.util.spec_from_file_location("bdp", ROOT / "samples" / "demo_pack" / "build_demo_pack.py")
    bdp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bdp)
    bdp.OUT = tmp_path_factory.mktemp("pack")
    bdp.main()
    db = get_db()
    for c in list(db.list_collection_names()):
        db[c].delete_many({})
    provider_mod.get_provider.cache_clear()
    for f in sorted(bdp.OUT.iterdir()):
        if f.name[:2].isdigit() and not f.name.startswith("90"):
            assert client.post("/api/ingest/file", files={"file": (f.name, f.read_bytes())}).status_code == 200
    return bdp


def test_work_and_bom_files_are_gold(loaded):
    db = get_db()
    assert db.records.count_documents({"record_type": "work_task", "layer": "silver", "trust.grade": "A"}) > 50
    assert db.records.count_documents({"record_type": "bom_line", "layer": "silver"}) == 29
    assert db.quarantine.count_documents({}) == 0


def test_hub_cards_live(loaded):
    cards = client.get("/api/amxg/hub").json()["cards"]
    assert set(cards) == {"events", "timelines", "docks", "tiered", "status", "machine", "dockboard", "bom", "script"}
    assert all(c["live"] for c in cards.values())


def test_events_feed_and_acknowledge(loaded):
    e = client.get("/api/amxg/events").json()
    assert {"Fault registry", "Issue register", "Work package"} <= set(e["by_source"])
    ev = e["items"][0]
    r = client.post(f"/api/amxg/events/{ev['id']}", json={"state": "acknowledged", "user": "tester", "assignee": "Dock 5 lead"})
    assert r.status_code == 200
    again = {i["id"]: i for i in client.get("/api/amxg/events").json()["items"]}[ev["id"]]
    assert again["state"] == "acknowledged" and again["assignee"] == "Dock 5 lead"
    assert client.post(f"/api/amxg/events/{ev['id']}", json={"state": "bogus"}).status_code == 400
    assert get_db().audit.count_documents({"action": "event_acknowledged"}) == 1


def test_timelines_filters(loaded):
    t = client.get("/api/amxg/timelines").json()
    assert t["counts"] == {"all": 16, "in_work": 8, "delivered": 6, "incoming": 2}
    inc = client.get("/api/amxg/timelines?filter=incoming").json()["rows"]
    assert {r["asset_id"] for r in inc} == {"61-5812", "60-1432"} and all(not r["segments"] for r in inc)
    # queues have a goal of 0 and are never flagged as over goal
    assert not any(s["over"] for r in t["rows"] for s in r["segments"] if s["is_queue"])


def test_tiered_drills_to_aircraft(loaded):
    root = client.get("/api/amxg/tiered").json()["root"]
    assert {o["name"] for o in root["children"]} == {"OC-ALC", "OO-ALC", "WR-ALC"}
    oc = next(o for o in root["children"] if o["name"] == "OC-ALC")
    b52 = next(w for w in oc["children"] if w["name"] == "B-52")
    depot = b52["children"][0]
    assert depot["level"] == "Depot line" and "Repair" in depot["kpis"]["over_plan_gates"]
    aircraft = [a for d in depot["children"] for a in d["children"]]
    assert len(aircraft) == depot["kpis"]["in_work"] == 8


def test_status_report_grid(loaded):
    s = client.get("/api/amxg/status").json()
    row = next(r for r in s["rows"] if r["asset_id"] == "61-3457")
    assert row["cells"]["Restore"]["state"].startswith("in_work")
    assert "B52-NC-LH01" in row["blocked_parts"]


def test_dockboard_tree(loaded):
    d = client.get("/api/amxg/dockboard").json()
    def leaves(n):
        return [n] if "attrs" in n else [x for c in n["children"] for x in leaves(c)]
    ls = leaves(d["tree"])
    assert len(ls) == d["tasks"] and {"Dock 1", "Off dock"} <= {c["name"] for c in d["tree"]["children"]}
    assert any(l["attrs"]["parts"] == "short" for l in ls)


def test_bom_tree_and_asset_zoom(loaded):
    b = client.get("/api/amxg/bom").json()
    def leaves(n):
        return [n] if "attrs" in n else [x for c in n["children"] for x in leaves(c)]
    by = {l["name"]: l["attrs"] for l in leaves(b["tree"])}
    assert by["B52-NC-LH01"]["availability"] == "blocked" and by["B52-NC-LH01"]["supplier"] == "Prairie Aerostructures"
    one = client.get("/api/amxg/bom?asset=61-3457").json()
    needed = {l["name"] for l in leaves(one["tree"]) if l["attrs"]["needed_by"]}
    assert {"B52-ENG-TF33", "B52-NC-LH01"} <= needed


def test_gate_script_found_work_reflows_and_mirrors(loaded):
    g = client.get("/api/amxg/script/60-7531").json()
    assert g["gate"] == "Repair" and g["goal_days"] == 61 and not g["pmss_connected"]
    before = g["reflowed_end"]
    r = client.post("/api/amxg/script/60-7531/found-work", json={"gate": "Repair", "work_package": "Hydraulics", "task": "Replace cracked bracket",
                                                                  "skill": "AG", "start_day": 50, "days": 9, "hours": 60, "user": "tester"})
    assert r.status_code == 200
    g2 = client.get("/api/amxg/script/60-7531").json()
    leak = next(x for x in g2["rows"] if x["task"] == "System leak check")
    assert leak["new_start"] == 59 and leak["shift"] == 4 and g2["reflowed_end"] > before   # pushed past the found work (days 50-58)
    open_task = next(x for x in g2["rows"] if x["status"] != "Done" and not x["found"])
    assert client.post("/api/amxg/script/60-7531/close", json={"task_id": open_task["task_id"], "user": "tester"}).status_code == 200
    g3 = client.get("/api/amxg/script/60-7531").json()
    assert next(x for x in g3["rows"] if x["task_id"] == open_task["task_id"])["status"] == "Done"
    assert {m["kind"] for m in g3["mirror"]} >= {"closure", "found_work"}
    assert client.post("/api/amxg/script/60-7531/close", json={"task_id": "nope", "user": "t"}).status_code == 404
