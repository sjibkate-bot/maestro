"""External risk feeds, tested on fixtures cut from the real responses of each service."""
import os
from pathlib import Path

os.environ["MONGO_URI"] = "mongomock://"
os.environ["AI_ENABLED"] = "false"
os.environ["RISK_FEEDS_ENABLED"] = "false"

import pytest
from fastapi.testclient import TestClient

from app import risk_feeds as rf
from app import supply_risk
from app.ai import provider as provider_mod
from app.db import get_db
from app.main import app

FX = Path(__file__).resolve().parent / "fixtures" / "feeds"
S = Path(__file__).resolve().parent.parent / "samples"
client = TestClient(app)
ROUTES = {"sdn.csv": "sdn.csv", "UK-Sanctions-List": "uk.csv", "gdacs.org": "gdacs.xml",
          "earthquake.usgs.gov": "usgs.json", "worldbank": "wb.json", "timelinevolraw": "gdelt_timeline.json"}


def fake_fetch(url, **kw):
    for frag, name in ROUTES.items():
        if frag in url:
            return (FX / name).read_text(encoding="utf-8")
    if "artlist" in url:
        return '{"articles": [{"title": "Port workers strike", "url": "https://example.org/a", "seendate": "20261118T120000Z"}]}'
    raise AssertionError(f"unexpected URL {url}")


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    db = get_db()
    for c in list(db.list_collection_names()):
        db[c].delete_many({})
    provider_mod.get_provider.cache_clear()
    monkeypatch.setattr(rf, "fetch", fake_fetch)
    monkeypatch.setattr(rf.time, "sleep", lambda s: None)
    yield


def load(*names, data=None):
    for n in names:
        r = client.post("/api/ingest/file", files={"file": (n, data if data is not None else (S / n).read_bytes())})
        assert r.status_code == 200, r.text


def test_parsers_on_real_formats():
    ofac = rf.parse_ofac((FX / "sdn.csv").read_text())
    assert ofac[0]["name"] == "AEROCARIBBEAN AIRLINES" and ofac[0]["program"] == "CUBA"
    assert any(e["type"].lower() == "individual" for e in ofac)
    uk = rf.parse_uk((FX / "uk.csv").read_text(encoding="utf-8"))
    assert uk and uk[0]["uid"] == "AFG0001" and "Afghanistan" in uk[0]["program"]
    assert len({(e["uid"], e["name"]) for e in uk}) == len(uk)            # repeated rows collapsed
    gd = rf.parse_gdacs((FX / "gdacs.xml").read_text())
    assert {e["level"] for e in gd} >= {"Orange", "Green"}
    q = rf.parse_usgs((FX / "usgs.json").read_text())
    assert q[0]["mag"] == 6.6 and q[0]["country_text"] == "Indonesia"
    wb = {r["iso3"]: r for r in rf.parse_worldbank((FX / "wb.json").read_text())}
    assert wb["DEU"]["value"] > 0 and "AIA" in wb                          # empty code resolved from the name
    days = rf.parse_gdelt_timeline((FX / "gdelt_timeline.json").read_text())
    assert len(days) >= 7


@pytest.mark.parametrize("name,iso", [("Taiwan", "TWN"), ("USA", "USA"), ("Viet Nam", "VNM"), ("Alaska", "USA"),
                                      ("UK", "GBR"), ("South Korea", "KOR"), ("Pacific-Antarctic Ridge", None)])
def test_country_resolution(name, iso):
    assert rf.resolve_country(name) == iso


def test_severity_scales():
    assert rf.stability_severity(0.8) == 0 and rf.stability_severity(-2.5) == 100
    assert rf.gdacs_severity("Red") == 90 and rf.gdacs_severity("Orange", "DR") == 36
    assert rf.quake_severity(6.6) == 70 and rf.quake_severity(4.6) == 20
    sev, ratio = rf.unrest_severity([10] * 20 + [30, 30, 30, 1])          # last point = today's partial count
    assert ratio == 3.0 and sev > 60


def test_sanctions_matching_is_local_and_entity_only():
    listed = [{"uid": "1", "name": "ANGLO-CARIBBEAN CO., LTD.", "type": "entity", "program": "CUBA"},
              {"uid": "2", "name": "BANCO NACIONAL DE CUBA", "type": "entity", "program": "CUBA"}]
    hits = rf.match_names(["Anglo Caribbean Company Limited", "Banco Nacional de Cuba S.A.", "Acme Components"], listed)
    assert {h["supplier"] for h in hits} == {"Anglo Caribbean Company Limited", "Banco Nacional de Cuba S.A."}


def test_run_all_feeds_end_to_end():
    load("supplier_master.csv", "purchase_orders.csv", "inventory.xlsx", "sales_orders.csv")
    # a supplier that appears on the (real) OFAC fixture
    load("extra.csv", data=b"Supplier,Tier,Country,Annual Spend\nAerocaribbean Airlines,1,Cuba,1000\n")
    runs = {r["feed"]: r for r in client.post("/api/feeds/run-all", json={"user": "test", "wait": True}).json()}
    assert all(r["status"] == "ok" for r in runs.values()), runs
    db = get_db()
    assert db.records.count_documents({"record_type": "risk_signal", "layer": "silver"}) == db.records.count_documents({"record_type": "risk_signal"})
    assert runs["ofac_sdn"]["signals"] == 1 and runs["gdacs"]["signals"] > 0
    assert db.records.count_documents({"source": "gdacs", "attributes.alert_level": "Green"}) == 0   # minor alerts dropped
    assert db.records.count_documents({"source": "usgs_quakes", "risk_score": {"$lt": 40}}) == 0       # M<5 dropped
    sups = {s["supplier"]: s for s in supply_risk.suppliers()}
    a = sups["Aerocaribbean Airlines"]
    assert a["sanctions_match"] and a["factors"]["geopolitical"] == 100 and a["actions"][0].startswith("Verify possible sanctions")
    assert sups["Initech Plastics"]["country_risk"]["stability"] > sups["Globex Metals"]["country_risk"]["stability"]  # Mexico > Germany
    assert sups["Stark Electronics"]["factors"]["geopolitical"] is None or sups["Stark Electronics"]["country_risk"]["stability"] is None  # no WB data for Taiwan
    v = client.get("/api/supply/signals").json()
    assert v["sanctions"][0]["supplier"] == "Aerocaribbean Airlines" and v["countries"]
    assert db.bronze_files.count_documents({"source_id": "feed:ofac_sdn"}) == 1
    assert get_db().audit.count_documents({"action": "feed_run"}) == 6


def test_feed_failure_is_recorded_not_raised(monkeypatch):
    def down(url, **kw):
        raise rf.httpx.ConnectError("network unreachable")
    monkeypatch.setattr(rf, "fetch", down)
    r = client.post("/api/feeds/gdacs/run", json={"user": "t", "wait": True}).json()
    assert r["status"] == "error" and "network unreachable" in r["error"]
    f = {x["id"]: x for x in client.get("/api/feeds").json()["feeds"]}
    assert f["gdacs"]["status"] == "error" and f["gdacs"]["last_success"] is None


def test_refresh_replaces_previous_signals():
    load("supplier_master.csv")
    client.post("/api/feeds/usgs_quakes/run", json={"user": "t", "wait": True})
    n = get_db().records.count_documents({"source": "usgs_quakes"})
    client.post("/api/feeds/usgs_quakes/run", json={"user": "t", "wait": True})
    assert get_db().records.count_documents({"source": "usgs_quakes"}) == n


def test_disable_feed():
    client.put("/api/feeds/gdelt_unrest", json={"enabled": False, "user": "t"})
    runs = client.post("/api/feeds/run-all", json={"user": "t", "wait": True}).json()
    assert "gdelt_unrest" not in {r["feed"] for r in runs}


def test_partial_gdelt_is_reported(monkeypatch):
    load("supplier_master.csv")
    def flaky(url, **kw):
        if "gdeltproject" in url and "Germany" in url:
            raise rf.httpx.HTTPStatusError("HTTP 429", request=None, response=None)
        return fake_fetch(url)
    monkeypatch.setattr(rf, "fetch", flaky)
    r = client.post("/api/feeds/gdelt_unrest/run", json={"user": "t", "wait": True}).json()
    assert r["status"] == "partial" and "Germany" in r["error"] and r["signals"] >= 1
