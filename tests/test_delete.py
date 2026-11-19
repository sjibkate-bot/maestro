"""Deleting loaded data: confirmation guard, every layer cleaned, older versions restored, audited."""
import os
from pathlib import Path

os.environ["MONGO_URI"] = "mongomock://"
os.environ["AI_ENABLED"] = "false"

import pytest
from fastapi.testclient import TestClient

from app import governance
from app.ai import provider as provider_mod
from app.db import get_db
from app.main import app

DIR = Path(__file__).resolve().parent.parent / "samples" / "maestro"
client = TestClient(app)


@pytest.fixture(autouse=True)
def clean():
    db = get_db()
    for c in ("records", "raw", "sources", "mappings", "quarantine", "certifications", "decisions",
              "audit", "drift", "bronze_files", "governance_config"):
        db[c].delete_many({})
    provider_mod.get_provider.cache_clear()
    yield


def load(name, source=None, data=None):
    data = data if data is not None else (DIR / name).read_bytes()
    r = client.post("/api/ingest/file", files={"file": (name, data)}, data={"source": source} if source else {})
    assert r.status_code == 200, r.text


def test_sources_listed_with_contents():
    load("B-52_Fixer_Chart_Data.xlsx")
    load("Dashboard_Supporting_Data.xlsx")
    ss = {s["source_id"]: s for s in client.get("/api/data/sources").json()}
    assert set(ss) == {"B-52_Fixer_Chart_Data.xlsx", "Dashboard_Supporting_Data.xlsx"}
    assert "gate_plan" in ss["B-52_Fixer_Chart_Data.xlsx"]["entities"] and ss["B-52_Fixer_Chart_Data.xlsx"]["files"] == 1


def test_wrong_or_missing_confirmation_deletes_nothing():
    load("Dashboard_Supporting_Data.xlsx")
    n = get_db().records.count_documents({})
    for body in ({"confirm": "yes", "user": "me"}, {"confirm": "Dashboard_Supporting_Data.xlsx", "user": ""}):
        r = client.post("/api/data/sources/Dashboard_Supporting_Data.xlsx/delete", json=body)
        assert r.status_code == 400
    assert client.post("/api/data/delete-all", json={"confirm": "delete all", "user": "me"}).status_code == 400
    assert get_db().records.count_documents({}) == n


def test_delete_one_source_from_every_layer():
    load("B-52_Fixer_Chart_Data.xlsx")
    load("Dashboard_Supporting_Data.xlsx")
    load("faults.csv", data=b"Tail_Number,Date_Logged,Fault_Code,Severity,Description,Technician_Assigned\n"
                            b"60-1234,2026-05-20,X999,Catastrophic,Bad code,SSgt A\n")
    assert governance.quarantine()
    client.post("/api/governance/products/depot_flow/certify", json={"user": "Owner"})
    client.post("/api/governance/products/fleet_health/certify", json={"user": "Owner"})
    pv = client.get("/api/data/delete-preview", params={"source": "faults.csv"}).json()
    assert pv["records"] == 1 and pv["quarantine_items"] == 1 and pv["products_affected"] == ["Fleet Health & Reliability"]
    r = client.post("/api/data/sources/faults.csv/delete", json={"confirm": "faults.csv", "user": "Tester", "reason": "test file"})
    assert r.status_code == 200 and r.json()["certifications_withdrawn"] == ["fleet_health"]
    db = get_db()
    assert db.records.count_documents({"source_id": "faults.csv"}) == 0
    assert db.raw.count_documents({"source_id": "faults.csv"}) == 0
    assert db.bronze_files.count_documents({"source_id": "faults.csv"}) == 0
    assert governance.quarantine("all") == []
    ps = {p["id"]: p for p in governance.products()}
    assert ps["depot_flow"]["certification"] == "CERTIFIED"          # untouched product keeps its certificate
    assert ps["fleet_health"]["certification"] == "PENDING"
    a = db.audit.find_one({"action": "data_delete"})
    assert a["user"] == "Tester" and a["detail"]["reason"] == "test file" and a["detail"]["records"] == 1


def test_deleting_newer_load_makes_older_version_current():
    load("Dashboard_Supporting_Data.xlsx", source="week1")
    load("Dashboard_Supporting_Data.xlsx", source="week2")
    db = get_db()
    assert db.records.count_documents({"source_id": "week1", "record_type": "asset_status", "layer": "history"}) == 16
    client.post("/api/data/sources/week2/delete", json={"confirm": "week2", "user": "Tester"})
    assert db.records.count_documents({"source_id": "week1", "record_type": "asset_status", "layer": "silver"}) == 16
    assert len(client.get("/api/maestro/fleet").json()["assets"]) == 16


def test_delete_all():
    load("B-52_Fixer_Chart_Data.xlsx")
    load("Dashboard_Supporting_Data.xlsx")
    r = client.post("/api/data/delete-all", json={"confirm": "DELETE ALL", "user": "Tester"})
    assert r.status_code == 200
    db = get_db()
    assert db.records.count_documents({}) == db.raw.count_documents({}) == db.bronze_files.count_documents({}) == 0
    assert client.get("/api/data/sources").json() == []
    assert client.get("/api/maestro/docks").json()["aircraft"] == []
    assert db.audit.find_one({"action": "data_delete"})["detail"]["all"] is True


def test_unknown_source_404():
    r = client.post("/api/data/sources/nope/delete", json={"confirm": "nope", "user": "Tester"})
    assert r.status_code == 404


def test_ai_answers_leave_the_overview_when_cleared_or_data_deleted():
    from app import governance
    d1 = governance.record_decision("names of aircraft", {"answer": "none", "confidence": .9}, {}, "test-model")
    did = d1["id"]
    assert client.get("/api/governance/overview").json()["latest_decision"]["question"] == "names of aircraft"
    assert client.post(f"/api/governance/decisions/{did}/clear", json={"user": "", "text": ""}).status_code in (400, 409, 422)
    assert client.post(f"/api/governance/decisions/{did}/clear", json={"user": "tester", "text": "stale"}).status_code == 200
    assert client.get("/api/governance/overview").json()["latest_decision"] is None
    hist = client.get("/api/governance/decisions").json()
    assert hist[0]["archived"] and hist[0]["archived_reason"] == "stale"          # still in the history
    governance.record_decision("second question", {"answer": "x", "confidence": .9}, {}, "test-model")
    load("purchase_orders.csv", data=(DIR.parent / "purchase_orders.csv").read_bytes())
    r = client.post("/api/data/delete-all", json={"confirm": "DELETE ALL", "user": "tester"})
    assert r.status_code == 200
    assert client.get("/api/governance/overview").json()["latest_decision"] is None
