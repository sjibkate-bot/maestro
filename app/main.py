"""FastAPI application: ingestion endpoints, analytics, AI Q&A and the web dashboard."""
import logging
import re
import uuid
from pathlib import Path
from typing import Any, Optional

import httpx
from fastapi import Body, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel

from . import analytics, governance, pdm
from .ai.claude import AIError
from .ai.provider import get_provider
from .connectors.files import ingest_file
from .connectors.remote import MqttSubscriber, ingest_iot, pull_api
from .db import get_db
from .models import RecordType

logging.basicConfig(level=logging.INFO)
app = FastAPI(title="Supply Chain AI Data Hub", version="0.1.0")
WEB = Path(__file__).resolve().parent.parent / "web"
_mqtt: dict[str, MqttSubscriber] = {}


def _source_id(name: Optional[str]) -> str:
    if name:
        return re.sub(r"[^a-zA-Z0-9_.-]", "-", name)[:80]
    return uuid.uuid4().hex[:12]


@app.get("/")
def index():
    # One application: Maestro. Supply analysis is its Supply module; data loads in Configuration.
    return RedirectResponse("/maestro#supply")


@app.get("/api/health")
def health():
    try:
        get_db().sources.count_documents({})
        db_ok = True
    except Exception:
        db_ok = False
    return {"status": "ok" if db_ok else "db_unreachable", "ai_provider": get_provider().name}


# ---------------- Ingestion ----------------

@app.post("/api/ingest/file")
async def upload_file(file: UploadFile = File(...), source: Optional[str] = Form(None),
                      record_type: Optional[RecordType] = Form(None)):
    """Excel, CSV, JSON, PDF, TXT/EML. The AI maps columns or extracts line items."""
    data = await file.read()
    try:
        results = ingest_file(file.filename, data, _source_id(source or file.filename),
                              record_type.value if record_type else None)
    except AIError as e:
        raise HTTPException(502, str(e))
    except Exception as e:  # malformed file
        logging.exception("ingest failed")
        raise HTTPException(400, f"Could not read {file.filename}: {e}")
    return {"file": file.filename, "batches": results}


class ApiPull(BaseModel):
    url: str
    source: Optional[str] = None
    kind: str = "api"  # "api" or "erp"
    headers: dict[str, str] = {}
    params: dict[str, Any] = {}
    username: Optional[str] = None
    password: Optional[str] = None
    records_path: Optional[str] = None  # e.g. "d.results" or "data.items"
    record_type: Optional[RecordType] = None


@app.post("/api/ingest/api")
def ingest_from_api(req: ApiPull):
    """Pull from a REST API or ERP OData endpoint (SAP, Dynamics, Oracle, NetSuite, Odoo...)."""
    try:
        return pull_api(source_id=_source_id(req.source or req.url), url=req.url,
                        source_type=req.kind, headers=req.headers, params=req.params,
                        auth=(req.username, req.password) if req.username else None,
                        records_path=req.records_path,
                        record_type_hint=req.record_type.value if req.record_type else None)
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Source request failed: {e}")
    except AIError as e:
        raise HTTPException(502, str(e))


@app.post("/api/ingest/iot/{source}")
def iot_push(source: str, payload: Any = Body(...)):
    """Devices / gateways POST JSON readings here (one object or a list)."""
    return ingest_iot(_source_id(source), payload)


class MqttReq(BaseModel):
    source: str
    host: str
    topic: str
    port: int = 1883
    username: Optional[str] = None
    password: Optional[str] = None


@app.post("/api/ingest/mqtt/start")
def mqtt_start(req: MqttReq):
    sid = _source_id(req.source)
    if sid in _mqtt:
        return {"status": "already running", "source": sid}
    sub = MqttSubscriber(sid, req.host, req.topic, req.port, req.username, req.password)
    sub.start()
    _mqtt[sid] = sub
    return {"status": "subscribed", "source": sid, "topic": req.topic}


@app.post("/api/ingest/mqtt/stop/{source}")
def mqtt_stop(source: str):
    sub = _mqtt.pop(_source_id(source), None)
    if not sub:
        raise HTTPException(404, "No such subscription")
    sub.stop()
    return {"status": "stopped"}


# ---------------- Data access ----------------

@app.get("/api/sources")
def sources():
    return list(get_db().sources.find({}, {"_id": 1, "source_type": 1, "record_count": 1,
                                           "last_ingested_at": 1}))


@app.get("/api/mappings")
def mappings():
    return list(get_db().mappings.find({}, {"_id": 0}))


@app.get("/api/records")
def records(record_type: Optional[RecordType] = None, sku: Optional[str] = None,
            supplier: Optional[str] = None, source: Optional[str] = None,
            limit: int = Query(100, le=1000), skip: int = 0):
    q: dict[str, Any] = {}
    if record_type:
        q["record_type"] = record_type.value
    if sku:
        q["sku"] = sku
    if supplier:
        q["supplier"] = supplier
    if source:
        q["source_id"] = source
    db = get_db()
    return {"total": db.records.count_documents(q),
            "items": list(db.records.find(q, {"_id": 0}).skip(skip).limit(limit))}


@app.delete("/api/sources/{source}")
def delete_source(source: str):
    db = get_db()
    n = db.records.delete_many({"source_id": source}).deleted_count
    db.raw.delete_many({"source_id": source})
    db.sources.delete_one({"_id": source})
    return {"deleted_records": n}


# ---------------- Analytics & AI ----------------

@app.get("/api/analytics/overview")
def overview():
    return analytics.overview()


@app.get("/api/analytics/{name}")
def analytic(name: str):
    fns = {"inventory": analytics.inventory_summary, "suppliers": analytics.supplier_performance,
           "shipments": analytics.shipment_summary, "demand": analytics.demand_summary,
           "iot": analytics.sensor_anomalies}
    if name not in fns:
        raise HTTPException(404, f"Unknown analytic. Options: {', '.join(fns)}")
    return fns[name]()


# ---------------- Maestro-SC (depot / maintenance) ----------------

@app.get("/maestro")
def maestro_page():
    return FileResponse(WEB / "maestro.html")


@app.get("/api/maestro/gates")
def maestro_gates():
    return [{"gate": g, "gate_number": pdm.GATE_NUMBER[g], "is_queue": pdm.IS_QUEUE[g]} for g in pdm.GATE_ORDER]


@app.get("/api/maestro/gate/{gate}")
def maestro_gate(gate: str, last_n: int = Query(pdm.LAST_N, ge=1, le=24)):
    if gate not in pdm.GATE_ORDER:
        raise HTTPException(404, f"Unknown gate. Options: {', '.join(pdm.GATE_ORDER)}")
    return pdm.gate_graph(gate, last_n)


@app.get("/api/maestro/machine")
def maestro_machine(last_n: int = Query(pdm.LAST_N, ge=1, le=24)):
    return pdm.machine(last_n)


@app.get("/api/maestro/docks")
def maestro_docks():
    return pdm.dock_overview()


@app.get("/api/maestro/fleet")
def maestro_fleet():
    return pdm.fleet_health()


@app.get("/api/maestro/alerts")
def maestro_alerts():
    return pdm.alerts()


# ---------------- Data governance ----------------

@app.get("/governance")
def governance_page():
    return FileResponse(WEB / "governance.html")


@app.get("/api/governance/overview")
def gov_overview():
    return governance.overview()


@app.post("/api/governance/run")
def gov_run():
    return governance.run()


@app.get("/api/governance/stage/{name}")
def gov_stage(name: str):
    try:
        return governance.stage(name)
    except KeyError:
        raise HTTPException(404, "Stages: plan, filter, clean, enrich, audit, monitor")


@app.put("/api/governance/config")
def gov_config(changes: dict = Body(...), user: str = Query("governance-council")):
    try:
        return governance.update_config(changes, user)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/governance/products")
def gov_products():
    return governance.products()


class Certify(BaseModel):
    user: str
    note: str = ""


@app.post("/api/governance/products/{pid}/certify")
def gov_certify(pid: str, body: Certify):
    try:
        return governance.certify(pid, body.user, body.note)
    except KeyError:
        raise HTTPException(404, "Unknown product")
    except ValueError as e:
        raise HTTPException(409, str(e))


@app.get("/api/governance/quarantine")
def gov_quarantine(status: str = "open"):
    return governance.quarantine(status)


class Resolution(BaseModel):
    user: str
    resolution: str


@app.post("/api/governance/quarantine/{qid}/close")
def gov_close(qid: str, body: Resolution):
    try:
        return governance.close_quarantine(qid, body.user, body.resolution)
    except KeyError:
        raise HTTPException(404, "Unknown quarantine item")
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/governance/records")
def gov_records(grade: Optional[str] = None, layer: Optional[str] = None, record_type: Optional[str] = None,
                limit: int = Query(100, le=1000)):
    q: dict[str, Any] = {}
    if grade:
        q["trust.grade"] = grade
    if layer:
        q["layer"] = layer
    if record_type:
        q["record_type"] = record_type
    return list(get_db().records.find(q, {"_id": 0, "cleaning": 0}).limit(limit))


@app.get("/api/governance/drill")
def gov_drill(product: Optional[str] = None, record_type: Optional[str] = None, layer: Optional[str] = None,
              grade: Optional[str] = None, issue: Optional[str] = None, source: Optional[str] = None,
              batch: Optional[str] = None, asset: Optional[str] = None, gold: bool = False,
              skip: int = 0, limit: int = Query(100, le=500)):
    try:
        return governance.drill(product, record_type, layer, grade, issue, source, batch, asset, gold, skip, limit)
    except KeyError:
        raise HTTPException(404, "Unknown product")


@app.get("/api/governance/record/{rid}")
def gov_record(rid: str):
    try:
        return governance.record_detail(rid)
    except Exception:
        raise HTTPException(404, "Unknown record")


@app.get("/api/governance/batches")
def gov_batches(blocked: Optional[bool] = None, source: Optional[str] = None):
    return governance.batches(blocked, source)


@app.get("/api/governance/batch/{batch_id}")
def gov_batch(batch_id: str):
    try:
        return governance.batch_detail(batch_id)
    except KeyError:
        raise HTTPException(404, "Unknown batch")


@app.get("/api/governance/diagnose")
def gov_diagnose(entities: str):
    return governance.diagnose([e.strip() for e in entities.split(",") if e.strip()])


@app.get("/api/governance/decisions")
def gov_decisions():
    return governance.decisions()


class DecisionAction(BaseModel):
    user: str
    text: str = ""


@app.post("/api/governance/decisions/{did}/{action}")
def gov_decide(did: str, action: str, body: DecisionAction):
    if action not in ("accept", "modify", "override"):
        raise HTTPException(404, "Actions: accept, modify, override")
    try:
        return governance.act_on_decision(did, action, body.user, body.text)
    except KeyError:
        raise HTTPException(404, "Unknown decision")
    except ValueError as e:
        raise HTTPException(409, str(e))


class Question(BaseModel):
    question: str


@app.post("/api/ask")
def ask(q: Question):
    """Ask a supply-chain question in plain language; answered from your data."""
    ov = analytics.overview()
    samples = {}
    proj = {"_id": 0, "batch_id": 0, "row_hash": 0, "cleaning": 0, "trust.issues": 0, "trust.dims": 0}
    for rt in ov["record_counts"]:
        flt = governance.gold_query({"record_type": rt}) if governance.governed() else {"record_type": rt}
        samples[rt] = list(get_db().records.find(flt, proj).limit(15))
    context = {"kpis": ov["kpis"], "record_counts": ov["record_counts"],
               "below_reorder_point": ov["inventory"]["below_reorder_point"][:30],
               "suppliers": ov["suppliers"]["suppliers"][:30],
               "late_po_lines": ov["suppliers"]["late_lines"][:30],
               "delayed_shipments": ov["shipments"]["delayed"][:30],
               "top_demand": ov["demand"]["top_skus"],
               "sensor_anomalies": ov["iot"]["anomalies"][:30],
               "samples": samples}
    if ov["record_counts"].get("gate_event") or ov["record_counts"].get("asset_status"):
        m = pdm.machine()
        context["depot"] = {
            "as_of": pdm.as_of(), "machine_last6": m["gates"], "total_avg_days": m["total_avg_days"],
            "last6_flow_days": m["last_n_flow_days"], "aircraft_in_work": pdm.dock_overview()["aircraft"],
            "alerts": pdm.alerts()["items"][:40],
            "fleet": [{k: a[k] for k in ("asset_id", "status", "mc_rate", "mtbm_actual", "mtbm_target")}
                      | {"weakest_subsystems": a["subsystems"][:3]} for a in pdm.fleet_health()["assets"]],
        }
        for k in ("gate_event", "asset_flow", "subsystem_health", "sensor_reading"):
            samples.pop(k, None)
    products = governance.products() if governance.governed() else []
    context["data_trust"] = [{k: p[k] for k in ("name", "grade", "score", "certification", "trust_label")}
                             for p in products]
    provider = get_provider()
    try:
        result = provider.answer(q.question, context).model_dump()
    except AIError as e:
        raise HTTPException(502, str(e))
    trust = governance.overall_trust() if products else {"score": None, "grade": None}
    data = {**trust, "products": context["data_trust"]}
    if provider.name == "rules":
        result["confidence"] = None
    decision = governance.record_decision(q.question, result, data, getattr(provider, "model", provider.name))
    return {**result, "decision": {k: decision[k] for k in ("id", "gate", "status", "data_trust", "model")}}
