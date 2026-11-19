"""Any-industry data: unknown files stay unknown, industry packs, entities registered from a
source, governance rules scoped per entity, and the generic data API the front end uses."""
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
IND = Path(__file__).resolve().parents[1] / "samples" / "industry"


@pytest.fixture(autouse=True)
def clean():
    db = get_db()
    for name in list(db.list_collection_names()):
        db[name].delete_many({})
    registry.load()                      # forget entities registered by other tests
    provider_mod.get_provider.cache_clear()


def upload(*names, mode="review"):
    files = [("files", (n, (IND / n).read_bytes())) for n in names]
    r = c.post("/api/batches", files=files, data={"user": "tester", "mode": mode})
    assert r.status_code == 200, r.text
    return r.json()


def test_unknown_industry_data_is_not_forced_into_a_near_entity():
    b = upload("retail_store_sales.csv", "hospital_bed_occupancy.csv")
    ents = {o["file"]: o["entity"] for o in b["objects"]}
    assert ents == {"retail_store_sales.csv": "other", "hospital_bed_occupancy.csv": "other"}
    assert all("register a new entity" in o["gate"][0] for o in b["objects"])
    assert get_db().records.count_documents({}) == 0


def test_industry_packs_map_erp_headers_and_publish():
    assert {p["id"] for p in c.get("/api/packs").json()} == {"manufacturing", "retail", "life_sciences"}
    for pid in ("manufacturing", "retail", "life_sciences"):
        r = c.post(f"/api/packs/{pid}/enable", json={"user": "tester"})
        assert r.status_code == 200, r.text
    assert c.post("/api/packs/retail/enable", json={"user": "tester"}).json()["unchanged"]     # idempotent
    b = upload("manufacturing_work_orders_sap.csv", "retail_store_sales.csv", "pharma_lots.csv")
    o = {x["file"]: x for x in b["objects"]}
    assert o["manufacturing_work_orders_sap.csv"]["entity"] == "work_order"
    assert o["retail_store_sales.csv"]["entity"] == "store_sales"
    assert o["pharma_lots.csv"]["entity"] == "batch_lot"
    wo = c.get(f"/api/objects/{o['manufacturing_work_orders_sap.csv']['_id']}").json()
    m = {f["name"]: (f["canonical_field"], f["status"]) for f in wo["fields"]}
    assert m["AUFNR"] == ("work_order_id", "AUTO") and m["GAMNG"] == ("quantity", "AUTO") and m["Shift Lead"][1] == "UNMAPPED"
    run = c.post(f"/api/batches/{b['_id']}/publish", json={"user": "tester"}).json()
    assert run["status"] == "COMPLETED", run
    # registered domains become governance domains with their own data product
    prods = {p["id"]: p for p in c.get("/api/governance/products").json()}
    assert {"manufacturing_data", "retail_data", "life_sciences_data"} <= set(prods)
    assert prods["retail_data"]["records"] == 240
    # the shift lead column was kept as an attribute and is queryable as ext.*
    rows = c.get("/api/entities/work_order/records?ext.Shift Lead=K. Osei&page_size=5").json()
    assert rows["total"] > 0 and rows["rows"][0]["ext.Shift Lead"] == "K. Osei"


def test_allowed_values_and_ranges_are_scoped_to_their_entity():
    c.post("/api/packs/life_sciences/enable", json={"user": "tester"})
    bad = (IND / "pharma_lots.csv").read_text().replace("Quarantined", "Destroyed", 1)
    r = c.post("/api/batches", files=[("files", ("pharma_lots.csv", bad.encode()))], data={"user": "t", "mode": "auto"})
    assert r.status_code == 200
    q = list(get_db().quarantine.find({}, {"reason": 1, "record_type": 1}))
    assert len(q) == 1 and "lot_status 'Destroyed'" in q[0]["reason"]
    # the core 'severity' list does not apply to a registered entity that has its own 'severity' field
    registry.create({"name": "incident", "domain": "operations", "key": ["incident_id"],
                     "fields": [{"name": "incident_id", "type": "text", "required": True},
                                {"name": "severity", "type": "text"}]}, "tester")
    from app.connectors.files import ingest_file
    ingest_file("incidents.csv", b"incident_id,severity\nI-1,sev2\n", "incidents", "incident")
    assert get_db().records.find_one({"record_type": "incident"})["trust"]["grade"] in ("A", "B")


def test_register_entity_from_an_unknown_source():
    b = upload("hospital_bed_occupancy.csv")
    oid = b["objects"][0]["_id"]
    prop = c.get(f"/api/objects/{oid}/propose-entity?domain=healthcare").json()
    assert prop["name"] == "hospital_bed_occupancy" and prop["domain"] == "healthcare"
    types = {f["name"]: f["type"] for f in prop["fields"]}
    assert types["census_date"] == "date" and types["beds_occupied"] == "number" and types["ward_code"] == "text"
    prop["name"], prop["key"], prop["key_mode"] = "ward_census", ["ward_code", "census_date"], "snapshot"
    assert c.post(f"/api/objects/{oid}/register-entity", json={"definition": prop}).status_code == 422   # who?
    v = c.post(f"/api/objects/{oid}/register-entity", json={"definition": prop, "user": "tester"}).json()
    assert v["entity"] == "ward_census" and v["status"] == "READY" and all(f["status"] == "ACCEPTED" for f in v["fields"])
    run = c.post(f"/api/batches/{b['_id']}/publish", json={"user": "tester"}).json()
    assert run["status"] == "COMPLETED" and run["records"] == 30
    d = c.get("/api/entities/ward_census").json()
    assert d["origin"] == "registered" and d["domain"] == "healthcare" and d["records"] == 30 and d["key"] == ["ward_code", "census_date"]
    # the next file with these columns maps by itself (column synonyms came from the source names)
    b2 = upload("hospital_bed_occupancy.csv")
    assert b2["objects"][0]["entity"] == "ward_census"
    # a type change while records exist, a core name and a type clash are refused
    bad = {**prop, "fields": [{**f, "type": "text"} if f["name"] == "beds_occupied" else f for f in prop["fields"]]}
    assert "cannot change" in c.post("/api/entities", json={"definition": bad, "user": "t"}).json()["detail"]
    assert "core entity" in c.post("/api/entities", json={"definition": {**prop, "name": "inventory"}, "user": "t"}).json()["detail"]
    clash = {"name": "x_thing", "fields": [{"name": "quantity", "type": "text"}]}
    assert "already used as number" in c.post("/api/entities", json={"definition": clash, "user": "t"}).json()["detail"]
    assert c.delete("/api/entities/ward_census?user=t").status_code == 409        # has records


def test_generic_data_api():
    c.post("/api/packs/retail/enable", json={"user": "tester"})
    b = upload("retail_store_sales.csv", mode="auto")
    assert b["objects"][0]["status"] == "PUBLISHED"
    cat = {e["name"]: e for e in c.get("/api/entities").json()}
    assert cat["store_sales"]["records"] == 240 and cat["store_sales"]["origin"] == "registered" and cat["inventory"]["origin"] == "core"
    d = c.get("/api/entities/store_sales").json()
    f = {x["name"]: x for x in d["fields"]}
    assert f["units"]["type"] == "number" and f["sale_date"]["required"] and d["key"] == ["store_id", "sku", "sale_date"]
    r = c.get("/api/entities/store_sales/records?store_id=S014&units__gte=30&sort=-units&page_size=5").json()
    assert r["total"] > 0 and r["rows"][0]["units"] >= r["rows"][-1]["units"] and all(x["store_id"] == "S014" for x in r["rows"])
    assert r["rows"][0]["_meta"]["grade"] in ("A", "B") and r["rows"][0]["sale_date"].startswith("2026-")
    assert c.get("/api/entities/store_sales/records?nope=1").status_code == 400
    assert c.get("/api/entities/not_a_thing/records").status_code == 404
    s = c.get("/api/entities/store_sales/summary").json()
    sf = {x["field"]: x for x in s["fields"]}
    assert s["records"] == 240 and sf["units"]["min"] >= 5 and sf["store_id"]["distinct"] == 3
    a = c.get("/api/entities/store_sales/aggregate?metric=sum&field=revenue&group_by=store_id,currency").json()
    assert {(x["store_id"], x["currency"]) for x in a["rows"]} == {("S001", "EUR"), ("S002", "EUR"), ("S014", "GBP")}
    m = c.get("/api/entities/store_sales/aggregate?metric=sum&field=units&date_field=sale_date&bucket=month&store_id=S001").json()
    assert [x["month"] for x in m["rows"]] == ["2026-10", "2026-11"]
    assert c.get("/api/entities/store_sales/aggregate?metric=sum&field=store_id").status_code == 400
    one = c.get(f"/api/records/{r['rows'][0]['_meta']['id']}").json()
    assert one["_entity"] == "store_sales" and one["_trust"]["grade"] in ("A", "B")


def test_governance_config_lists_registered_domains_and_rules():
    c.post("/api/packs/retail/enable", json={"user": "tester"})
    cfg = c.get("/api/governance/config").json()
    assert "store_sales" in cfg["domains"]["retail"]["entities"]
    assert cfg["keys"]["store_sales"] == ["event", ["store_id", "sku", "sale_date"]]
    assert cfg["ranges"]["promotion.discount_pct"] == [0, 100]
    assert any(p["id"] == "retail_data" for p in cfg["products"])
