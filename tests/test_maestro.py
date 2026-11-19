"""Maestro-SC tests on the real reference workbooks (B-52 Fixer data, dashboard data)."""
import os
from pathlib import Path

os.environ["MONGO_URI"] = "mongomock://"
os.environ["AI_ENABLED"] = "false"

import pytest
from fastapi.testclient import TestClient

from app.ai import provider as provider_mod
from app.db import get_db
from app.main import app
from app.models import BoardAsset, DocumentExtraction

DIR = Path(__file__).resolve().parent.parent / "samples" / "maestro"
client = TestClient(app)


def upload(name):
    with open(DIR / name, "rb") as f:
        r = client.post("/api/ingest/file", files={"file": (name, f)})
    assert r.status_code == 200, r.text
    return r.json()["batches"]


@pytest.fixture(autouse=True)
def loaded():
    db = get_db()
    for c in ("records", "raw", "sources", "mappings"):
        db[c].delete_many({})
    provider_mod.get_provider.cache_clear()
    upload("B-52_Fixer_Chart_Data.xlsx")
    upload("Dashboard_Supporting_Data.xlsx")
    yield


def test_workbook_sheets_are_recognised():
    db = get_db()
    assert db.records.count_documents({"record_type": "asset_flow"}) == 16  # 10 in work + 6 produced
    assert db.records.count_documents({"record_type": "asset_flow", "status": "produced"}) == 6
    assert db.records.count_documents({"record_type": "gate_plan"}) == 5
    assert db.records.count_documents({"record_type": "production_schedule"}) == 27
    # dictionary block above the table is skipped; side-by-side tables merged
    assert db.records.count_documents({"record_type": "subsystem_health"}) == 96
    prop = db.records.find_one({"record_type": "subsystem_health", "asset_id": "60-1234", "subsystem": "Propulsion"})
    assert prop["health_score"] == 64.5 and prop["critical_fault_count"] == 2
    assert db.records.count_documents({"record_type": "asset_status"}) == 16
    assert db.records.count_documents({"record_type": "sensor_reading"}) == 48
    assert db.records.count_documents({"record_type": "fault"}) == 10


def test_post_dock_gate_graph_follows_process_document():
    g = client.get("/api/maestro/gate/Post Dock").json()
    done = [b for b in g["bars"] if not b["in_work"]]
    assert [b["asset_id"] for b in done] == ["61-1573", "61-7258", "60-6548", "60-1965", "60-4488", "60-4003"]
    assert [b["actual_days"] for b in done] == [60, 51, 59, 47, 48, 47]
    assert g["last_n_avg"] == 52.0 and g["requirement"] == 54
    assert g["subtitle"] == "Gate 5: Post Dock"
    assert g["wip"] == {"plan": 2.5, "actual": 2, "over_plan": False}
    cur = {b["asset_id"]: b for b in g["bars"] if b["in_work"]}
    assert cur["60-1234"]["actual_days"] == 44 and cur["60-1234"]["remaining_days"] == 10


def test_last_x_is_configurable():
    g = client.get("/api/maestro/gate/Post Dock", params={"last_n": 3}).json()
    assert g["last_n_avg"] == round((47 + 48 + 47) / 3, 1)


def test_dock_overview_current_gates():
    cards = {c["asset_id"]: c for c in client.get("/api/maestro/docks").json()["aircraft"]}
    assert len(cards) == 10
    assert cards["61-4657"]["gate"] == "Repair" and cards["61-4657"]["day"] == 38
    assert cards["61-3457"]["gate"] == "Queue to Post Dock"   # restore done, post dock not started
    assert cards["61-2847"]["gate"] == "Queue to Repair"      # repair start is planned for after as-of date
    assert cards["60-1432"]["gate"] == "Awaiting induction"
    assert cards["60-1234"]["flow_days"] == 181 and cards["60-1234"]["amrep_days"] == 239
    assert "D1 says 61-3457" in cards["60-7890"]["data_quality"]


def test_fleet_health_and_alerts():
    f = client.get("/api/maestro/fleet").json()
    a = next(x for x in f["assets"] if x["asset_id"] == "60-1234")
    assert a["band"]["code"] == "FMC" and a["subsystems"][0]["subsystem"] == "Propulsion"
    al = client.get("/api/maestro/alerts").json()
    assert al["critical"] >= 4 and al["total"] == al["critical"] + al["warning"]


def test_scanned_board_without_ai_makes_no_junk():
    [b] = upload("Notional_Status_Board.pdf")
    assert b["records"] == 0 and "need AI" in b["note"]


def test_scanned_board_with_ai(monkeypatch):
    class FakeAI:
        name = "fake"
        def extract_document(self, *, text=None, pdf_bytes=None, filename=""):
            assert pdf_bytes[:4] == b"%PDF"
            return DocumentExtraction(document_kind="status board", records=[], assets=[BoardAsset(
                asset_id="60-0052", location="Dock 4", current_gate="Restore", day_in_gate=14, gate_goal_days=14,
                flow_day=139, flow_goal_days=227, queue_days=15, amrep_date="2019-12-27",
                next_big_thing=["Install gear"], known_repairs=[], notes=[])])
    monkeypatch.setattr("app.connectors.files.get_provider", lambda: FakeAI())
    monkeypatch.setattr("app.pipeline.get_provider", lambda: FakeAI())
    [b] = upload("Notional_Status_Board.pdf")
    assert b["assets"] == 1
    rec = get_db().records.find_one({"record_type": "asset_status", "asset_id": "60-0052"})
    assert rec["location"] == "Dock 4" and rec["attributes"]["board"]["flow_day"] == 139


def _snapshot(as_of):
    """The Fixer workbook re-dated, as if it were a different weekly snapshot."""
    import io
    import openpyxl
    wb = openpyxl.load_workbook(DIR / "B-52_Fixer_Chart_Data.xlsx")
    for ws in wb.worksheets:
        if ws["J1"].value == "Today's Date":
            ws["K1"].value = as_of
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_older_snapshot_does_not_overwrite_newer():
    from datetime import datetime
    r = client.post("/api/ingest/file", files={"file": ("old.xlsx", _snapshot(datetime(2030, 9, 2)))})
    batches = r.json()["batches"]
    tail = next(b for b in batches if b.get("asset_id") == "60-1234")
    assert tail["records"] == 0 and "newer snapshot" in tail["note"]
    flow = get_db().records.find_one({"record_type": "asset_flow", "asset_id": "60-1234"})
    assert flow["as_of"].year == 2030 and flow["as_of"].month == 9 and flow["as_of"].day == 30


def test_ai_failure_on_one_sheet_keeps_the_rest(monkeypatch):
    from app.ai.claude import AIError
    from app.connectors import sheets

    def boom(*a, **k):
        raise AIError("Claude API error 401: invalid x-api-key")
    monkeypatch.setattr(sheets, "parse_machine_info", boom)
    r = client.post("/api/ingest/file", files={"file": ("x.xlsx", (DIR / "B-52_Fixer_Chart_Data.xlsx").read_bytes())})
    assert r.status_code == 200
    batches = r.json()["batches"]
    assert any(b.get("error", "").endswith("invalid x-api-key") for b in batches)
    assert sum(b["records"] for b in batches) > 100
