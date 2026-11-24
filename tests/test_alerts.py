"""Alert Center: alerts for every industry, worked as a queue, with rules and notifications."""
import importlib.util
import os
from pathlib import Path

os.environ["MONGO_URI"] = "mongomock://"
os.environ["AI_ENABLED"] = "false"
os.environ["RISK_FEEDS_ENABLED"] = "false"

import pytest
from fastapi.testclient import TestClient

from app import alerts, registry
from app.ai import provider as provider_mod
from app.db import get_db
from app.main import app

c = TestClient(app)
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def retail(tmp_path_factory):
    spec = importlib.util.spec_from_file_location("retail_alerts", ROOT / "samples/retail_pack/build_retail_pack.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    m.OUT = tmp_path_factory.mktemp("retail_alerts")
    m.main()
    return m.OUT


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    for k in ("ALERT_WEBHOOK_URL", "SMTP_HOST", "ALERT_EMAIL_TO"):
        monkeypatch.delenv(k, raising=False)
    db = get_db()
    for n in list(db.list_collection_names()):
        db[n].delete_many({})
    registry.load()
    alerts._cache.clear()
    provider_mod.get_provider.cache_clear()


def load(paths, mode="auto"):
    r = c.post("/api/batches", files=[("files", (p.name, p.read_bytes())) for p in paths], data={"user": "T", "mode": mode})
    assert r.status_code == 200, r.text
    return r.json()


def load_retail(out):
    core = sorted(p for p in out.iterdir() if p.name[:2] in {f"{i:02d}" for i in range(1, 13)})
    b = load(core, "review")
    oid = next(o["_id"] for o in b["objects"] if o["status"] != "READY")
    c.patch(f"/api/objects/{oid}/fields", json={"column": "Site City", "action": "ACCEPTED", "user": "T"})
    c.patch(f"/api/objects/{oid}/fields", json={"column": "State / Province", "action": "EXTENSION", "extension_key": "state_province", "user": "T"})
    assert c.post(f"/api/batches/{b['_id']}/publish", json={"user": "T"}).json()["status"] == "COMPLETED"


def by(items, **kw):
    return [x for x in items if all(x[k] == v for k, v in kw.items())]


def test_no_data_no_alerts():
    a = c.get("/api/alerts").json()
    assert a["counts"]["open"] == 0 and a["items"] == []


def test_retail_alerts_and_queue(retail):
    load_retail(retail)
    a = c.get("/api/alerts").json()
    cats = {x["category"] for x in a["items"]}
    assert {"Stock", "Supplier alert", "Supplier risk"} <= cats and "Fleet & depot" not in cats
    lime = by(a["items"], category="Stock")[0]
    assert lime["subject"].startswith("FRS-LIM-40") and lime["level"] == "critical" and lime["link"] == "/supply#coverage"
    held = [x for x in by(a["items"], category="Supplier alert") if "Port Newark" in x["text"]][0]
    assert held["level"] == "critical" and held["subject"] == "Guntur Chilli Traders"
    assert held["link"] == "/supply#suppliers/Guntur%20Chilli%20Traders"
    open0 = a["counts"]["open"]

    k = held["key"]
    assert c.post(f"/api/alerts/{k}/acknowledge", json={"user": "SJ"}).json()["status"] == "ACKNOWLEDGED"
    assert c.post(f"/api/alerts/{k}/assign", json={"user": "SJ"}).status_code == 422          # owner needed
    r = c.post(f"/api/alerts/{k}/assign", json={"user": "SJ", "owner": "Food Safety", "due": "2026-12-01"}).json()
    assert (r["owner"], r["due"]) == ("Food Safety", "2026-12-01")
    assert c.post(f"/api/alerts/{k}/resolve", json={"user": "SJ"}).status_code == 422         # note needed
    c.post(f"/api/alerts/{k}/resolve", json={"user": "SJ", "note": "Lot released after sampling"})
    a = c.get("/api/alerts").json()
    assert a["counts"]["open"] == open0 - 1 and a["counts"]["resolved"] == 1
    assert by(c.get("/api/alerts?status=RESOLVED").json()["items"], key=k)[0]["note"] == "Lot released after sampling"
    assert [e["action"] for e in get_db().audit.find({"detail.alert": k}).sort("at", 1)] == \
        ["alert_acknowledge", "alert_assign", "alert_resolve"]
    c.post(f"/api/alerts/{k}/reopen", json={"user": "SJ"})
    assert by(c.get("/api/alerts").json()["items"], key=k)[0]["status"] == "NEW"
    # a resolved alert that gets worse comes back
    c.post(f"/api/alerts/{k}/resolve", json={"user": "SJ", "note": "x"})
    get_db().alert_state.update_one({"_id": k}, {"$set": {"level_at": "info"}})
    assert by(c.get("/api/alerts").json()["items"], key=k)[0]["status"] == "NEW"
    assert c.post("/api/alerts/nope/acknowledge", json={"user": "SJ"}).status_code == 404
    assert c.post(f"/api/alerts/{k}/explode", json={"user": "SJ"}).status_code == 404
    # filters
    assert {x["level"] for x in c.get("/api/alerts?level=critical").json()["items"]} == {"critical"}
    assert {x["category"] for x in c.get("/api/alerts?category=Stock").json()["items"]} == {"Stock"}


def test_rules_change_alerts(retail):
    load_retail(retail)
    okra = lambda: by(c.get("/api/alerts?category=Stock").json()["items"], subject=next(
        x["subject"] for x in c.get("/api/alerts?category=Stock").json()["items"] if x["subject"].startswith("FRZ-OKR")))[0]
    assert okra()["level"] == "critical"                      # 4.9 days, below the default 7
    assert c.put("/api/admin/alert-rules", json={"cover_critical_days": 3}).status_code == 200
    assert okra()["level"] == "warning"
    assert c.put("/api/admin/alert-rules", json={"cover_critical_days": 30}).status_code == 422   # above warning
    assert c.put("/api/admin/alert-rules", json={"supplier_levels": ["Sky high"]}).status_code == 422
    assert c.put("/api/admin/alert-rules", json={"nope": 1}).status_code == 422
    n_crit = len(by(c.get("/api/alerts").json()["items"], category="Supplier risk"))
    c.put("/api/admin/alert-rules", json={"supplier_levels": ["Critical", "High"]})
    assert len(by(c.get("/api/alerts").json()["items"], category="Supplier risk")) > n_crit
    cats = {**alerts.DEFAULT_RULES["categories"], "Stock": False}
    c.put("/api/admin/alert-rules", json={"categories": cats})
    assert not by(c.get("/api/alerts").json()["items"], category="Stock")
    assert c.get("/api/admin/alert-rules").json()["rules"]["categories"]["Stock"] is False


def test_aviation_and_data_quality_alerts_and_module_filter():
    out = ROOT / "samples/demo_pack/out"
    b = load([out / "03_Fleet_Health_2030-09-16.xlsx", out / "90_Governance_Demo_Bad_Fault_Entries.csv"])
    bad = next(o for o in b["objects"] if o["status"] == "MAPPING_REQUIRED")      # the date column needs a decision
    c.patch(f"/api/objects/{bad['_id']}/fields", json={"column": "Date_Logged", "action": "ACCEPTED", "user": "T"})
    c.post(f"/api/batches/{b['_id']}/publish", json={"user": "T"})
    items = alerts.listing()["items"]
    cats = {x["category"] for x in items}
    assert {"Fleet & depot", "Data quality"} <= cats
    q = by(items, category="Data quality")[0]
    assert q["subject"] == "fault" and "quarantine" in q["text"] and q["link"].startswith("/governance#drill?layer=quarantine")
    # a supply-only user sees neither; a depot user sees fleet alerts
    assert alerts.listing(["supply", "ai"])["items"] == []
    assert {x["category"] for x in alerts.listing(["depot"])["items"]} == {"Fleet & depot"}


def test_notifications(retail, monkeypatch):
    load_retail(retail)
    assert alerts.notify()["detail"].startswith("No channel")
    sent = []

    class R:
        status_code = 200
    import httpx
    monkeypatch.setattr(httpx, "post", lambda url, json, timeout: sent.append((url, json["text"])) or R())
    monkeypatch.setenv("ALERT_WEBHOOK_URL", "https://hooks.example.test/abc")
    monkeypatch.setenv("MAESTRO_PUBLIC_URL", "https://maestro.example.test")
    assert c.get("/api/admin/alert-rules").json()["channels"] == {"webhook": True, "email": False}
    assert c.post("/api/admin/alerts/notify", json={"test": True}).json()["sent"] == {"webhook": True}
    r = c.post("/api/admin/alerts/notify", json={}).json()
    crit = c.get("/api/alerts?level=critical").json()["counts"]["critical"]
    assert r["alerts"] == crit > 0 and r["sent"] == {"webhook": True}
    text = sent[-1][1]
    assert text.startswith(f"Maestro-SC: {crit} new alert(s)") and "[CRITICAL]" in text and "https://maestro.example.test/maestro#alerts" in text
    assert c.post("/api/admin/alerts/notify", json={}).json()["alerts"] == 0      # each alert is sent once
    assert len(sent) == 2
