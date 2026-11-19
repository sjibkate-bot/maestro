"""End-to-end tests on an in-memory MongoDB with the rule-based provider,
plus a stubbed Claude provider for the AI document path."""
import json
import os
from pathlib import Path

os.environ["MONGO_URI"] = "mongomock://"
os.environ["AI_ENABLED"] = "false"

import pytest
from fastapi.testclient import TestClient

from app.ai import provider as provider_mod
from app.db import get_db
from app.main import app
from app.models import DocumentExtraction, ExtractedRecord, MappingResult, ColumnMapping

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_db():
    db = get_db()
    for c in ("records", "raw", "sources", "mappings"):
        db[c].delete_many({})
    provider_mod.get_provider.cache_clear()
    yield


def upload(name, **form):
    with open(SAMPLES / name, "rb") as f:
        r = client.post("/api/ingest/file", files={"file": (name, f)}, data=form)
    assert r.status_code == 200, r.text
    return r.json()["batches"]


def test_excel_both_sheets_mapped_to_inventory():
    batches = upload("inventory.xlsx")
    assert {b["sheet"] for b in batches} == {"Stock", "SAP_MARD"}
    stock = next(b for b in batches if b["sheet"] == "Stock")
    assert stock["record_type"] == "inventory"
    assert stock["mapping"]["Item Code"] == "sku"
    assert stock["mapping"]["Reorder Point"] == "reorder_point"
    sap = next(b for b in batches if b["sheet"] == "SAP_MARD")
    assert sap["mapping"]["MATNR"] == "sku" and sap["mapping"]["EISBE"] == "reorder_point"
    inv = client.get("/api/analytics/inventory").json()
    assert inv["sku_locations"] > 0 and inv["inventory_value"] > 0


def test_csv_purchase_orders_and_supplier_kpis():
    [b] = upload("purchase_orders.csv")
    assert b["record_type"] == "purchase_order"
    assert b["mapping"]["Vendor"] == "supplier" and b["mapping"]["Received On"] == "delivered_date"
    sup = client.get("/api/analytics/suppliers").json()
    globex = next(s for s in sup["suppliers"] if s["supplier"] == "Globex Metals")
    assert globex["on_time_rate"] is None or globex["on_time_rate"] < 0.5  # always late in samples
    assert sup["late_lines"]


def test_json_odata_payload_shipments():
    [b] = upload("shipments_api.json", record_type="shipment")
    assert b["records"] == 25 and b["record_type"] == "shipment"


def test_iot_push_and_anomaly_detection():
    readings = json.loads((SAMPLES / "iot_readings.json").read_text())
    r = client.post("/api/ingest/iot/coldchain", json=readings)
    assert r.status_code == 200 and r.json()["records"] == 240
    iot = client.get("/api/analytics/iot").json()
    assert any(a["device_id"] == "reefer-02" and a["value"] == 14.5 for a in iot["anomalies"])


def test_mapping_is_cached_per_layout():
    upload("purchase_orders.csv")
    upload("purchase_orders.csv", source="po-second-load")
    assert get_db().mappings.count_documents({}) == 1


def test_pdf_tables_without_ai():
    batches = upload("purchase_order.pdf")
    assert sum(b["records"] for b in batches) == 3


def test_overview_and_ask_without_ai():
    upload("inventory.xlsx")
    upload("purchase_orders.csv")
    ov = client.get("/api/analytics/overview").json()
    assert ov["kpis"]["total_records"] == 100
    ans = client.post("/api/ask", json={"question": "What is at risk?"}).json()
    assert "AI is disabled" in ans["answer"]


class FakeAI:
    name = "fake"

    def map_columns(self, columns, sample_rows):
        return MappingResult(record_type="other", notes="",
                             mappings=[ColumnMapping(source_column=c, canonical_field=None) for c in columns])

    def extract_document(self, *, text=None, pdf_bytes=None, filename=""):
        assert text and "PO-4512" in text
        blank = {f: None for f in ExtractedRecord.model_fields}
        return DocumentExtraction(document_kind="supplier email", assets=[], records=[
            ExtractedRecord(**{**blank, "record_type": "purchase_order", "order_id": "PO-4512",
                               "sku": "SKU-0007", "quantity": 300, "supplier": "Globex Metals",
                               "promised_date": "2026-11-28"})])

    def answer(self, question, context):
        raise NotImplementedError


def test_unstructured_text_uses_ai(monkeypatch):
    monkeypatch.setattr("app.pipeline.get_provider", lambda: FakeAI())
    [b] = upload("supplier_email.txt")
    assert b["document_kind"] == "supplier email" and b["records"] == 1
    rec = client.get("/api/records", params={"record_type": "purchase_order"}).json()["items"][0]
    assert rec["order_id"] == "PO-4512" and rec["promised_date"].startswith("2026-11-28")


def test_claude_provider_request_shape(monkeypatch):
    """Verify the Claude call (model, structured output, fallbacks) without network."""
    from app.ai import claude as claude_mod

    captured = {}

    class Block:
        type = "text"
        text = json.dumps({"record_type": "inventory", "notes": "",
                           "mappings": [{"source_column": "MATNR", "canonical_field": "sku"},
                                        {"source_column": "X", "canonical_field": "bogus"}]})

    class Msg:
        stop_reason = "end_turn"
        content = [Block()]

    class Stream:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get_final_message(self): return Msg()

    class FakeBetaMessages:
        def stream(self, **kw):
            captured.update(kw)
            return Stream()

    p = claude_mod.ClaudeProvider.__new__(claude_mod.ClaudeProvider)
    p.model = "claude-opus-5-5"
    p.client = type("C", (), {"beta": type("B", (), {"messages": FakeBetaMessages()})()})()
    res = p.map_columns(["MATNR", "X"], [{"MATNR": "A1", "X": 1}])
    assert captured["model"] == "claude-opus-5-5"
    assert captured["output_format"] is MappingResult
    assert captured["fallbacks"] == "default"
    assert res.mappings[0].canonical_field == "sku" and res.mappings[1].canonical_field is None
