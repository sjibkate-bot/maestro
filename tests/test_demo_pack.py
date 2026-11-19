"""The customer demo pack builds and tells the intended story."""
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
def pack(tmp_path_factory):
    spec = importlib.util.spec_from_file_location("bdp", ROOT / "samples" / "demo_pack" / "build_demo_pack.py")
    bdp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bdp)
    bdp.OUT = tmp_path_factory.mktemp("pack")
    bdp.main()
    return bdp


@pytest.fixture(autouse=True)
def clean():
    db = get_db()
    for c in list(db.list_collection_names()):
        db[c].delete_many({})
    provider_mod.get_provider.cache_clear()


def load_all(out: Path):
    for f in sorted(out.iterdir()):
        if f.name[:2].isdigit() and not f.name.startswith("90"):
            r = client.post("/api/ingest/file", files={"file": (f.name, f.read_bytes())})
            assert r.status_code == 200, (f.name, r.text)


def test_pack_story(pack):
    load_all(pack.OUT)
    ov = client.get("/api/governance/overview").json()
    assert ov["kpis"]["quarantine"] == 0 and ov["kpis"]["blocked_at_ingest"] == 0
    assert set(ov["layers"]["silver"]["grade_mix"]) <= {"A", "D"} and ov["layers"]["silver"]["grade_mix"].get("D", 0) == 0
    docks = client.get("/api/maestro/docks").json()
    assert docks["as_of"].startswith("2030-09-16") and len(docks["aircraft"]) == 10
    held = [c["dock"] for c in docks["aircraft"] if c["dock"]]
    assert len(held) == len(set(held))                                      # one aircraft per dock
    assert not any(c["amrep_risk"] for c in docks["aircraft"])              # AMREP typo fixed
    g3 = client.get("/api/maestro/gate/Repair").json()
    assert g3["wip"] == {"plan": 2.83, "actual": 3, "over_plan": True}
    sup = {s["supplier"]: s for s in client.get("/api/supply/suppliers").json()}
    assert sup["Prairie Aerostructures"]["rating_class"]["label"] in ("High", "Critical")
    assert any(a["level"] == "critical" for a in client.get("/api/maestro/alerts").json()["items"])
    assert get_db().records.count_documents({"record_type": "production_summary"}) > 20


def test_bad_file_is_quarantined(pack):
    load_all(pack.OUT)
    bad = pack.OUT / "90_Governance_Demo_Bad_Fault_Entries.csv"
    client.post("/api/ingest/file", files={"file": (bad.name, bad.read_bytes())})
    codes = {q["reason_code"] for q in client.get("/api/governance/quarantine").json()}
    assert codes == {"MISSING_KEY", "INVALID_CODE", "INVALID_ID"}


def test_every_customer_change_is_logged(pack):
    import csv
    log = list(csv.DictReader(open(pack.OUT / "Cleaning_Log.csv")))
    kinds = {r["kind"] for r in log}
    assert {"fix", "synthetic", "derived", "question"} <= kinds
    assert any("64-4003" in r["before"] for r in log) and any("AMREP year typo" in r["reason"] for r in log)
