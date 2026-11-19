"""Data governance: Bronze/Silver/Gold, trust score, quarantine, certification, decision gate."""
import io
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
USER = {"user": "Test Owner"}


@pytest.fixture(autouse=True)
def clean():
    db = get_db()
    for c in ("records", "raw", "sources", "mappings", "quarantine", "certifications", "decisions",
              "audit", "drift", "bronze_files", "governance_config"):
        db[c].delete_many({})
    provider_mod.get_provider.cache_clear()
    yield


def upload(name, data=None):
    data = data if data is not None else (DIR / name).read_bytes()
    r = client.post("/api/ingest/file", files={"file": (name, data)})
    assert r.status_code == 200, r.text
    return r.json()["batches"]


def faults_csv(rows):
    head = "Tail_Number,Date_Logged,Fault_Code,Severity,Description,Technician_Assigned\n"
    return (head + "\n".join(rows)).encode()


def test_bronze_keeps_original_file_and_rows():
    upload("B-52_Fixer_Chart_Data.xlsx")
    db = get_db()
    f = db.bronze_files.find_one({})
    assert f["content"] == (DIR / "B-52_Fixer_Chart_Data.xlsx").read_bytes() and len(f["sha256"]) == 64
    tail = db.raw.find_one({"meta.sheet": "60-4003"})
    assert tail["payload"]["cells"][0][3] == "60-4003" and tail["payload_sha256"]
    assert all(r.get("trust") and r.get("layer") for r in db.records.find())


def test_gate1_blocks_unrecognised_schema():
    batches = upload("Dashboard_Supporting_Data.xlsx")
    hs = next(b for b in batches if b["sheet"] == "Health Score")
    assert hs["records"] == 0 and hs["blocked"] == 4 and "Gate 1" in hs["note"]
    assert get_db().raw.find_one({"meta.sheet": "Health Score"})["payload"]  # still kept in Bronze
    assert client.get("/api/governance/overview").json()["kpis"]["blocked_at_ingest"] == 4


def test_real_data_issues_are_found_but_not_quarantined():
    upload("B-52_Fixer_Chart_Data.xlsx")
    upload("Dashboard_Supporting_Data.xlsx")
    db = get_db()
    st = db.records.find_one({"record_type": "asset_status", "asset_id": "60-1234"})
    assert any(i["code"] == "STATUS_CONFLICT" for i in st["trust"]["issues"]) and st["trust"]["grade"] == "B"
    fl = db.records.find_one({"record_type": "asset_flow", "asset_id": "60-7890"})
    assert any(i["code"] == "ID_MISMATCH" for i in fl["trust"]["issues"])
    ps = {p["id"]: p for p in governance.products()}
    assert ps["depot_flow"]["grade"] == "A" and ps["fleet_health"]["certification"] == "PENDING"
    # gate graph still built from Gold and now carries its trust label
    g = client.get("/api/maestro/gate/Post Dock").json()
    assert g["last_n_avg"] == 52.0 and g["trust"]["product"] == "Depot Flow & Gate Performance"


def test_hard_rule_quarantines_and_keeps_out_of_gold():
    upload("Dashboard_Supporting_Data.xlsx")
    upload("faults.csv", faults_csv(["60-1234,2026-05-20,X999,Catastrophic,Bad severity code,SSgt A",
                                     "BADTAIL,2026-05-21,F012,Major,Unknown tail,SSgt B"]))
    q = governance.quarantine()
    codes = {x["reason_code"] for x in q}
    assert codes == {"INVALID_CODE", "INVALID_ID"}
    assert all(x["steward"] == "Maintenance Data Steward" and x["status"] == "open" for x in q)
    faults = client.get("/api/maestro/fleet").json()["faults"]
    assert not any(f["fault_code"] == "X999" for f in faults)          # modules read Gold only
    assert governance.products()[0]["quarantined"] >= 0
    ov = client.get("/api/governance/overview").json()
    assert ov["kpis"]["quarantine"] == 2 and ov["kpis"]["review_queue"] == 2


def test_fix_at_source_resolves_quarantine():
    upload("Dashboard_Supporting_Data.xlsx")
    upload("faults.csv", faults_csv(["60-1234,2026-05-20,X999,Catastrophic,Bad code,SSgt A"]))
    assert len(governance.quarantine()) == 1
    # corrected record re-sent by the source: same key, valid severity
    upload("faults_fixed.csv", faults_csv(["60-1234,2026-05-20,X999,Critical,Bad code,SSgt A"]))
    assert governance.quarantine() == []
    assert governance.quarantine("resolved")[0]["resolution"].startswith("replaced by a corrected record")


def test_duplicates_removed_from_silver():
    upload("Dashboard_Supporting_Data.xlsx")
    n = get_db().records.count_documents({"record_type": "fault", "layer": "silver"})
    upload("again.xlsx", (DIR / "Dashboard_Supporting_Data.xlsx").read_bytes())
    db = get_db()
    assert db.records.count_documents({"record_type": "fault", "layer": "silver"}) == n
    assert db.records.count_documents({"record_type": "fault", "layer": "duplicate"}) == n
    assert db.records.count_documents({"record_type": "asset_status", "layer": "history"}) == 16


def test_certification_gate_and_recertify():
    upload("B-52_Fixer_Chart_Data.xlsx")
    r = client.post("/api/governance/products/depot_flow/certify", json=USER)
    assert r.status_code == 200 and r.json()["certification"] == "CERTIFIED"
    upload("again.xlsx", (DIR / "B-52_Fixer_Chart_Data.xlsx").read_bytes())
    p = next(p for p in governance.products() if p["id"] == "depot_flow")
    assert p["certification"] == "RECERTIFY"
    assert any(a["action"] == "certify" for a in get_db().audit.find())


def test_certification_blocked_below_b():
    upload("B-52_Fixer_Chart_Data.xlsx")
    client.put("/api/governance/config", params={"user": "council"},
               json={"grades": {"A": 101, "B": 100.5, "C": 99}})
    r = client.post("/api/governance/products/depot_flow/certify", json=USER)
    assert r.status_code == 409 and "Gate 2" in r.json()["detail"]


def test_config_weights_must_total_100():
    r = client.put("/api/governance/config", json={"weights": {"completeness": 50, "validity": 10, "timeliness": 10,
                                                               "consistency": 10, "uniqueness": 10, "provenance": 5}})
    assert r.status_code == 400


@pytest.mark.parametrize("grade,conf,verdict", [
    ("A", 0.9, "RECOMMEND"), ("A", 0.7, "RECOMMEND_WITH_CAVEAT"), ("A", 0.3, "ADVISORY"),
    ("B", 0.9, "RECOMMEND_WITH_CAVEAT"), ("B", 0.7, "ADVISORY"), ("B", 0.3, "WITHHOLD"),
    ("C", 0.99, "WITHHOLD"), ("D", 0.99, "WITHHOLD"), ("A", None, "WITHHOLD")])
def test_decision_gate_matrix(grade, conf, verdict):
    assert governance.decision_gate(grade, conf)["verdict"] == verdict


def test_decisions_accept_modify_override():
    upload("B-52_Fixer_Chart_Data.xlsx")
    d = governance.record_decision("q", {"answer": "a", "confidence": 0.9}, {"grade": "A", "score": 99}, "m")
    assert d["gate"]["verdict"] == "RECOMMEND" and d["status"] == "PENDING"
    r = client.post(f"/api/governance/decisions/{d['id']}/override", json=USER)
    assert r.status_code == 409  # override needs a reason
    r = client.post(f"/api/governance/decisions/{d['id']}/override", json={**USER, "text": "parts already on order"})
    assert r.json()["status"] == "OVERRIDDEN"
    m = client.get("/api/governance/stage/monitor").json()["monitor"]
    assert m["override_rate"] == 100.0


def test_ask_without_ai_is_withheld():
    upload("B-52_Fixer_Chart_Data.xlsx")
    j = client.post("/api/ask", json={"question": "What is at risk?"}).json()
    assert j["decision"]["gate"]["verdict"] == "WITHHOLD" and j["decision"]["status"] == "WITHHELD"


def test_audit_balance_accounts_for_every_row():
    upload("B-52_Fixer_Chart_Data.xlsx")
    upload("Dashboard_Supporting_Data.xlsx")
    bal = client.get("/api/governance/stage/audit").json()["balance"]
    assert bal and all(b["balanced"] for b in bal)


def test_all_stage_views_load():
    upload("B-52_Fixer_Chart_Data.xlsx")
    for s in ("plan", "filter", "clean", "enrich", "audit", "monitor"):
        assert client.get(f"/api/governance/stage/{s}").status_code == 200


def test_drift_compares_same_sheet_only():
    from pathlib import Path as P
    samples = P(__file__).resolve().parent.parent / "samples"
    upload("inventory.xlsx", (samples / "inventory.xlsx").read_bytes())   # two different sheets
    assert get_db().drift.count_documents({}) == 0
    import openpyxl
    wb = openpyxl.load_workbook(samples / "inventory.xlsx")
    for row in wb["Stock"].iter_rows(min_row=2):
        row[3].value = (row[3].value or 0) * 3                          # next week's quantities tripled
    buf = io.BytesIO(); wb.save(buf)
    upload("inventory_week2.xlsx", buf.getvalue())
    drift = list(get_db().drift.find({}))
    assert [d["field"] for d in drift] == ["quantity"] and drift[0]["change_pct"] > 100


def test_drill_down_facets_and_record_lineage():
    upload("B-52_Fixer_Chart_Data.xlsx")
    upload("Dashboard_Supporting_Data.xlsx")
    upload("faults.csv", faults_csv(["60-1234,2026-05-20,X999,Catastrophic,Bad code,SSgt A"]))
    d = client.get("/api/governance/drill", params={"product": "fleet_health", "layer": "quarantine"}).json()
    assert d["total"] == 1 and d["facets"]["issue"] == {"INVALID_CODE": 1}
    rid = d["items"][0]["id"]
    r = client.get(f"/api/governance/record/{rid}").json()
    assert r["lineage"]["file"]["filename"] == "faults.csv"
    assert r["lineage"]["source_row"]["Severity"] == "Catastrophic"           # exact Bronze row
    assert r["lineage"]["gold"]["included"] is False and r["quarantine"]["reason_code"] == "INVALID_CODE"
    # every Post Dock record of one tail is reachable by asset
    a = client.get("/api/governance/drill", params={"asset": "60-4003", "record_type": "gate_event"}).json()
    assert a["total"] == 10
    t = client.get(f"/api/governance/record/{a['items'][0]['id']}").json()
    assert t["lineage"]["batch"]["template"] == "pdm_tail_sheet" and t["lineage"]["source_row"]["sheet_cells"][0][3] == "60-4003"


def test_issue_filter_and_batch_view():
    upload("B-52_Fixer_Chart_Data.xlsx")
    d = client.get("/api/governance/drill", params={"issue": "DATES_REVERSED"}).json()
    assert d["total"] >= 1 and all(i["record_type"] == "gate_event" for i in d["items"])
    upload("Dashboard_Supporting_Data.xlsx")
    blocked = client.get("/api/governance/batches", params={"blocked": True}).json()
    assert [b["sheet"] for b in blocked] == ["Health Score"]
    b = client.get(f"/api/governance/batch/{blocked[0]['batch_id']}").json()
    assert b["rows_received"] == 4 and b["columns"][0] == "Health Score Range"


def test_empty_module_explains_why():
    upload("Dashboard_Supporting_Data.xlsx")   # fleet data only, no depot tracker
    d = client.get("/api/governance/diagnose", params={"entities": "asset_flow,gate_event"}).json()
    assert d["gold_records"] == 0 and "No asset_flow, gate_event records" in d["reasons"][0]
    ps = {p["id"]: p for p in governance.products()}
    assert ps["depot_flow"]["certification"] == "NO DATA"


def test_tail_number_suffix_is_standardised():
    upload("Dashboard_Supporting_Data.xlsx")
    upload("faults.csv", faults_csv(["60-1234 (R),2026-05-22,F012,Major,Bus 2 overload,SSgt A"]))
    r = get_db().records.find_one({"fault_code": "F012", "date_logged": {"$gte": __import__("datetime").datetime(2026, 5, 22)}})
    assert r["asset_id"] == "60-1234" and r["attributes"]["asset_suffix"] == "R" and r["layer"] == "silver"
