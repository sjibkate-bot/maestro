"""FastAPI application: ingestion endpoints, analytics, AI Q&A and the web dashboard."""
import logging
from contextlib import asynccontextmanager
import os
import re
import uuid
from pathlib import Path
from typing import Any, Optional

import httpx
from fastapi import Body, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel

from . import amxg, analytics, dataapi, governance, packs, pdm, registry, risk_feeds, studio, supply_risk
from .ai.claude import AIError
from .ai.provider import get_provider
from .connectors.files import ingest_file
from .connectors.remote import MqttSubscriber, ingest_iot, pull_api
from .db import get_db
from .models import ENTITIES

logging.basicConfig(level=logging.INFO)
@asynccontextmanager
async def lifespan(_app):
    from . import registry
    registry.load()                # registered entities and enabled industry packs
    risk_feeds.start_scheduler()   # daily public risk feeds (RISK_FEEDS_ENABLED / RISK_FEEDS_HOUR)
    yield


app = FastAPI(title="Maestro-SC", version="0.2.0", lifespan=lifespan)
WEB = Path(__file__).resolve().parent.parent / "web"
_mqtt: dict[str, MqttSubscriber] = {}


def _rt(record_type: Optional[str]) -> Optional[str]:
    """Entity names are runtime data (core + registered + industry packs), so validate here."""
    if not record_type:
        return None
    if record_type not in ENTITIES:
        raise HTTPException(400, f"Unknown entity {record_type!r}. See GET /api/entities")
    return record_type


def _source_id(name: Optional[str]) -> str:
    if name:
        return re.sub(r"[^a-zA-Z0-9_.-]", "-", name)[:80]
    return uuid.uuid4().hex[:12]


@app.get("/")
def index():
    # One application: Maestro. Supply analysis is its Supply module; data loads in Configuration.
    return RedirectResponse("/supply")


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
                      record_type: Optional[str] = Form(None)):
    """Excel, CSV, JSON, PDF, TXT/EML. The AI maps columns or extracts line items."""
    data = await file.read()
    try:
        results = ingest_file(file.filename, data, _source_id(source or file.filename),
                              _rt(record_type))
    except AIError as e:
        raise HTTPException(502, str(e))
    except Exception as e:  # malformed file
        logging.exception("ingest failed")
        raise HTTPException(400, f"Could not read {file.filename}: {e}")
    return {"file": file.filename, "batches": results}


# ---------------------------------------------------------------- ingestion batches & Mapping Studio
@app.post("/api/batches")
async def create_batch(files: list[UploadFile] = File(...), name: Optional[str] = Form(None),
                       mode: str = Form("review"), user: Optional[str] = Form(None),
                       record_type: Optional[str] = Form(None)):
    """Upload several files as one ingestion batch. Tables are staged for Mapping Studio
    (mode=review) or published straight away (mode=auto). Layouts with an active mapping
    profile publish automatically; template sheets and documents publish directly.
    To show progress, a client can send the first file here and the rest one at a time
    to POST /api/batches/{id}/files."""
    if not files:
        raise HTTPException(400, "Select at least one file")
    if mode not in ("review", "auto"):
        raise HTTPException(400, "mode must be review or auto")
    b = studio.begin_batch(name or f"Batch {len(files)} file(s)", user or "unknown", mode)
    out = await _ingest_into_batch(b, files, record_type)
    return studio.batch_view(b["_id"]) | {"load": out}


@app.post("/api/batches/{batch_id}/files")
async def add_batch_files(batch_id: str, files: list[UploadFile] = File(...), record_type: Optional[str] = Form(None)):
    """Add files to an existing batch (same mode and user). Used to load file by file with progress."""
    b = get_db().ingest_batches.find_one({"_id": batch_id})
    if not b:
        raise HTTPException(404, "Batch not found")
    out = await _ingest_into_batch(b, files, record_type)
    v = studio.batch_view(batch_id)
    return {"_id": batch_id, "load": out, "counts": v["counts"], "objects": len(v["objects"]), "rows": v["rows"]}


async def _ingest_into_batch(b: dict, files: list[UploadFile], record_type: Optional[str]) -> list[dict]:
    out = []
    for f in files:
        data = await f.read()
        token = studio.STAGING.set({"batch_id": b["_id"], "mode": b.get("mode", "review"), "user": b.get("created_by")})
        try:
            res = ingest_file(f.filename, data, _source_id(f.filename), _rt(record_type))
            studio.register_direct(b["_id"], f.filename, _source_id(f.filename), res)
            out.append({"file": f.filename, "results": res})
        except Exception as e:
            logging.exception("batch ingest failed")
            out.append({"file": f.filename, "error": str(e)})
        finally:
            studio.STAGING.reset(token)
    studio.finish_batch(b["_id"], [{"file": f.filename} for f in files], out)
    return out


@app.get("/api/batches")
def list_batches():
    return studio.batches()


@app.get("/api/batches/{batch_id}")
def get_batch(batch_id: str):
    try:
        return studio.batch_view(batch_id)
    except KeyError:
        raise HTTPException(404, "Batch not found")


@app.post("/api/batches/{batch_id}/publish")
def publish_batch(batch_id: str, body: dict = Body(default={})):
    try:
        return studio.publish_batch(batch_id, body.get("user") or "")
    except KeyError:
        raise HTTPException(404, "Batch not found")
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/batches/{batch_id}/delete-preview")
def batch_delete_preview(batch_id: str):
    try:
        return studio.delete_loaded_preview(batch_id=batch_id)
    except KeyError:
        raise HTTPException(404, "Batch not found")


@app.post("/api/batches/{batch_id}/delete")
def delete_batch(batch_id: str, body: dict = Body(...)):
    """Delete everything this batch loaded. body: {confirm: "DELETE", user, reason?}"""
    try:
        return studio.delete_loaded(batch_id, None, body.get("confirm", ""), body.get("user") or "", body.get("reason") or "")
    except KeyError:
        raise HTTPException(404, "Batch not found")
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.get("/api/objects/{object_id}/delete-preview")
def object_delete_preview(object_id: str):
    try:
        return studio.delete_loaded_preview(object_id=object_id)
    except KeyError:
        raise HTTPException(404, "Source object not found")


@app.post("/api/objects/{object_id}/delete")
def delete_object(object_id: str, body: dict = Body(...)):
    """Delete one table of a batch (staged or published). body: {confirm: "DELETE", user, reason?}"""
    try:
        return studio.delete_loaded(None, object_id, body.get("confirm", ""), body.get("user") or "", body.get("reason") or "")
    except KeyError:
        raise HTTPException(404, "Source object not found")
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.post("/api/objects/{object_id}/publish")
def publish_object(object_id: str, body: dict = Body(default={})):
    """Validate, govern and publish one table. Recorded as a publish run of its batch."""
    o = get_db().source_objects.find_one({"_id": object_id})
    if not o:
        raise HTTPException(404, "Source object not found")
    try:
        return studio.publish_batch(o["batch_id"], body.get("user") or "", only=[object_id])
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/batches/{batch_id}/runs")
def batch_runs(batch_id: str):
    return list(get_db().publish_runs.find({"batch_id": batch_id}).sort("started_at", -1))


@app.get("/api/objects/{object_id}")
def get_object(object_id: str):
    try:
        return studio.object_view(object_id)
    except KeyError:
        raise HTTPException(404, "Source object not found")


def _studio_call(fn, *a, **kw):
    try:
        fn(*a, **kw)
        return studio.object_view(a[0])
    except KeyError as e:
        raise HTTPException(404, f"Not found: {e}")
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.patch("/api/objects/{object_id}/entity")
def set_object_entity(object_id: str, body: dict = Body(...)):
    return _studio_call(studio.set_entity, object_id, body.get("entity"), body.get("user") or "")


@app.patch("/api/objects/{object_id}/fields")
def field_action(object_id: str, body: dict = Body(...)):
    """body: column, action (ACCEPTED | IGNORED | EXTENSION | RESET), canonical_field, extension_key, user"""
    return _studio_call(studio.field_action, object_id, body.get("column"), body.get("action"), body.get("user") or "",
                        body.get("canonical_field"), body.get("extension_key"))


@app.post("/api/objects/{object_id}/accept-high-confidence")
def accept_high(object_id: str, body: dict = Body(default={})):
    return _studio_call(studio.accept_high_confidence, object_id, body.get("user") or "", int(body.get("minimum") or 90))


@app.post("/api/objects/{object_id}/profile")
def save_profile(object_id: str, body: dict = Body(default={})):
    return _studio_call(studio.save_profile, object_id, body.get("user") or "")


@app.get("/api/objects/{object_id}/propose-entity")
def propose_entity(object_id: str, name: Optional[str] = None, domain: Optional[str] = None):
    """Draft a new entity from an unrecognised source (fields, types, key, required)."""
    try:
        return studio.propose_entity(object_id, name, domain)
    except KeyError:
        raise HTTPException(404, "Source object not found")


@app.post("/api/objects/{object_id}/register-entity")
def register_entity(object_id: str, body: dict = Body(...)):
    """body: {definition: <entity definition, e.g. an edited proposal>, user}. Maps the source to it."""
    try:
        studio.register_entity(object_id, body.get("definition") or {}, body.get("user") or "")
        return studio.object_view(object_id)
    except KeyError:
        raise HTTPException(404, "Source object not found")
    except ValueError as e:
        raise HTTPException(422, str(e))


# ---------------------------------------------------------------- entity registry, industry packs, data API
@app.get("/api/entities")
def entities():
    """Every entity the platform knows (core + registered + packs) with domain, origin and record count."""
    return registry.catalog()


@app.post("/api/entities")
def create_entity(body: dict = Body(...)):
    """Register an entity (or a new version of one). body: {definition, user}."""
    try:
        return registry.create(body.get("definition") or {}, body.get("user") or "")
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.get("/api/entities/{entity}")
def describe_entity(entity: str):
    try:
        return registry.describe(entity)
    except KeyError:
        raise HTTPException(404, f"Unknown entity {entity}")


@app.delete("/api/entities/{entity}")
def retire_entity(entity: str, user: str = Query(...)):
    try:
        return registry.retire(entity, user)
    except KeyError:
        raise HTTPException(404, f"{entity} is not a registered entity")
    except ValueError as e:
        raise HTTPException(409, str(e))


_RESERVED = {"q", "sort", "page", "page_size", "layer", "fields", "group_by", "metric", "field", "date_field", "bucket", "limit"}


def _data_call(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except KeyError as e:
        raise HTTPException(404, f"Unknown entity or record {e}")
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/entities/{entity}/records")
def entity_records(entity: str, request: Request, q: Optional[str] = None, sort: Optional[str] = None, page: int = 1,
                   page_size: int = 50, layer: str = "gold", fields: Optional[str] = None):
    """Rows of any entity. Filters: field=value or field__op=value (ne gt gte lt lte in contains exists); ext.key=value."""
    filters = {k: v for k, v in request.query_params.items() if k not in _RESERVED}
    return _data_call(dataapi.records, entity, filters=filters, q=q, sort=sort, page=page, page_size=page_size,
                      layer=layer, fields=fields.split(",") if fields else None)


@app.get("/api/entities/{entity}/summary")
def entity_summary(entity: str, layer: str = "gold"):
    return _data_call(dataapi.summary, entity, layer)


@app.get("/api/entities/{entity}/aggregate")
def entity_aggregate(entity: str, request: Request, metric: str = "count", field: Optional[str] = None,
                     group_by: Optional[str] = None, date_field: Optional[str] = None, bucket: Optional[str] = None,
                     layer: str = "gold", limit: int = 200):
    filters = {k: v for k, v in request.query_params.items() if k not in _RESERVED}
    return _data_call(dataapi.aggregate, entity, group_by=group_by.split(",") if group_by else None, metric=metric,
                      field=field, date_field=date_field, bucket=bucket, filters=filters, layer=layer, limit=limit)


@app.get("/api/records/{rid}")
def one_record(rid: str):
    return _data_call(dataapi.record, rid)


@app.get("/api/packs")
def list_packs():
    return packs.list_packs()


@app.post("/api/packs/{pid}/enable")
def enable_pack(pid: str, body: dict = Body(default={})):
    try:
        return packs.enable(pid, body.get("user") or "")
    except KeyError:
        raise HTTPException(404, "Unknown pack")
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.get("/api/profiles")
def mapping_profiles():
    return studio.profiles()


class ApiPull(BaseModel):
    url: str
    source: Optional[str] = None
    kind: str = "api"  # "api" or "erp"
    headers: dict[str, str] = {}
    params: dict[str, Any] = {}
    username: Optional[str] = None
    password: Optional[str] = None
    records_path: Optional[str] = None  # e.g. "d.results" or "data.items"
    record_type: Optional[str] = None


@app.post("/api/ingest/api")
def ingest_from_api(req: ApiPull):
    """Pull from a REST API or ERP OData endpoint (SAP, Dynamics, Oracle, NetSuite, Odoo...)."""
    try:
        return pull_api(source_id=_source_id(req.source or req.url), url=req.url,
                        source_type=req.kind, headers=req.headers, params=req.params,
                        auth=(req.username, req.password) if req.username else None,
                        records_path=req.records_path,
                        record_type_hint=_rt(req.record_type))
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
def records(record_type: Optional[str] = None, sku: Optional[str] = None,
            supplier: Optional[str] = None, source: Optional[str] = None,
            limit: int = Query(100, le=1000), skip: int = 0):
    q: dict[str, Any] = {}
    if record_type:
        q["record_type"] = _rt(record_type)
    if sku:
        q["sku"] = sku
    if supplier:
        q["supplier"] = supplier
    if source:
        q["source_id"] = source
    db = get_db()
    return {"total": db.records.count_documents(q),
            "items": list(db.records.find(q, {"_id": 0}).skip(skip).limit(limit))}


@app.get("/api/data/sources")
def data_sources():
    """Loaded sources with what each holds (Configuration -> Loaded data)."""
    return governance.sources_summary()


@app.get("/api/data/delete-preview")
def data_delete_preview(source: Optional[str] = None):
    return governance.delete_preview(source)


class DeleteRequest(BaseModel):
    confirm: str
    user: str
    reason: str = ""


def _delete(source: Optional[str], body: DeleteRequest):
    try:
        return governance.delete_data(source, body.confirm, body.user, body.reason)
    except KeyError:
        raise HTTPException(404, "Unknown source")
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/data/sources/{source}/delete")
def data_delete_source(source: str, body: DeleteRequest):
    """Delete one source from Bronze, Silver and Gold. `confirm` must repeat the source name."""
    return _delete(source, body)


@app.post("/api/data/delete-all")
def data_delete_all(body: DeleteRequest):
    """Delete all loaded data. `confirm` must be DELETE ALL."""
    return _delete(None, body)


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


@app.get("/static/studio.js", include_in_schema=False)
def studio_js():
    return FileResponse(WEB / "studio.js", media_type="text/javascript")


@app.get("/static/amxg.js", include_in_schema=False)
def amxg_js():
    return FileResponse(WEB / "amxg.js", media_type="text/javascript")


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


# ---------------------------------------------------------------- AMXG modules
@app.get("/api/amxg/hub")
def amxg_hub():
    return amxg.hub()


@app.get("/api/amxg/events")
def amxg_events():
    return amxg.events()


@app.post("/api/amxg/events/{event_id}")
def amxg_event_update(event_id: str, body: dict = Body(...)):
    try:
        return amxg.update_event(event_id, body.get("state", "acknowledged"), body.get("user") or "unknown",
                                 body.get("assignee"), body.get("note"))
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/amxg/timelines")
def amxg_timelines(filter: str = Query("all", pattern="^(all|in_work|delivered|incoming)$")):
    return amxg.timelines(filter)


@app.get("/api/amxg/tiered")
def amxg_tiered():
    return amxg.tiered()


@app.get("/api/amxg/status")
def amxg_status():
    return amxg.status_report()


@app.get("/api/amxg/dockboard")
def amxg_dockboard():
    return amxg.dockboard()


@app.get("/api/amxg/bom")
def amxg_bom(asset: Optional[str] = None):
    return amxg.bom(asset or None)


@app.get("/api/amxg/script/{asset}")
def amxg_script(asset: str, gate: Optional[str] = None):
    return amxg.gate_script(asset, gate)


@app.post("/api/amxg/script/{asset}/close")
def amxg_close(asset: str, body: dict = Body(...)):
    try:
        return amxg.close_task(asset, body["task_id"], body.get("user") or "unknown")
    except KeyError:
        raise HTTPException(404, "task not found")


@app.post("/api/amxg/script/{asset}/found-work")
def amxg_found(asset: str, body: dict = Body(...)):
    try:
        return amxg.add_found_work(asset, body.get("gate"), body.get("work_package"), body.get("task"), body.get("skill", ""),
                                   int(body.get("start_day") or 0), int(body.get("days") or 0), float(body.get("hours") or 0),
                                   body.get("user") or "unknown", body.get("part_number") or None)
    except ValueError as e:
        raise HTTPException(400, str(e))


# ---------------- Supply analysis (risk) ----------------

@app.get("/supply")
def supply_page():
    return FileResponse(WEB / "supply.html")


@app.get("/api/supply/overview")
def supply_overview():
    return supply_risk.overview()


@app.get("/api/supply/suppliers")
def supply_suppliers():
    return supply_risk.suppliers()


@app.get("/api/supply/supplier")
def supply_supplier(name: str):
    try:
        return supply_risk.supplier(name)
    except KeyError:
        raise HTTPException(404, "Unknown supplier")


@app.get("/api/supply/network")
def supply_network():
    return supply_risk.network()


@app.get("/api/supply/coverage")
def supply_coverage(buffer_days: float = Query(7, ge=0, le=180)):
    return supply_risk.coverage(buffer_days)


@app.get("/api/supply/delivery")
def supply_delivery():
    return supply_risk.delivery()


@app.get("/api/supply/scenarios")
def supply_scenarios():
    return supply_risk.scenarios()


@app.post("/api/supply/whatif")
def supply_whatif(body: dict = Body(default={})):
    try:
        return supply_risk.whatif(float(body.get("delay_days") or 0), float(body.get("demand_pct") or 0),
                                  float(body.get("inventory_loss_pct") or 0), body.get("scenario_id") or None,
                                  int(body["horizon_days"]) if body.get("horizon_days") else None)
    except KeyError:
        raise HTTPException(404, "Scenario not found")


@app.get("/api/supply/decisions")
def supply_decisions():
    return supply_risk.decisions()


@app.post("/api/supply/decisions")
def supply_decide(body: dict = Body(...)):
    try:
        return supply_risk.record_decision(body, body.get("user") or "")
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.get("/api/supply/plans")
def supply_plans(supplier: Optional[str] = None):
    return supply_risk.plans(supplier)


class PlanIn(BaseModel):
    supplier: str
    strategy: str
    action: str
    owner: Optional[str] = None
    due: Optional[str] = None
    target_rating: Optional[int] = None
    user: str


@app.post("/api/supply/plans")
def supply_plan_create(body: PlanIn):
    try:
        return supply_risk.create_plan(body.model_dump(), body.user)
    except ValueError as e:
        raise HTTPException(400, str(e))


class PlanStatus(BaseModel):
    status: str
    user: str
    note: str = ""


@app.post("/api/supply/plans/{pid}/status")
def supply_plan_status(pid: str, body: PlanStatus):
    try:
        return supply_risk.update_plan(pid, body.status, body.user, body.note)
    except KeyError:
        raise HTTPException(404, "Unknown plan")
    except ValueError as e:
        raise HTTPException(400, str(e))


# ---------------- External risk feeds ----------------

@app.get("/api/feeds")
def feeds_list():
    return {"running": risk_feeds.is_running(), "feeds": risk_feeds.feeds(),
            "schedule": {"enabled": os.getenv("RISK_FEEDS_ENABLED", "true").lower() == "true",
                         "hour_utc": int(os.getenv("RISK_FEEDS_HOUR", "2"))}}


class FeedRun(BaseModel):
    user: str = "manual"
    wait: bool = False   # True: run now and return the results (used by tests and scripts)


@app.post("/api/feeds/run-all")
def feeds_run_all(body: FeedRun):
    if body.wait:
        return risk_feeds.run_all(body.user or "manual")
    return risk_feeds.run_in_background(body.user or "manual")


@app.post("/api/feeds/{feed_id}/run")
def feeds_run(feed_id: str, body: FeedRun):
    try:
        if body.wait:
            return risk_feeds.run_feed(feed_id, body.user or "manual")
        return risk_feeds.run_in_background(body.user or "manual", feed_id)
    except KeyError:
        raise HTTPException(404, "Unknown feed")


class FeedSetting(BaseModel):
    enabled: bool
    user: str


@app.put("/api/feeds/{feed_id}")
def feeds_set(feed_id: str, body: FeedSetting):
    try:
        return risk_feeds.set_enabled(feed_id, body.enabled, body.user)
    except KeyError:
        raise HTTPException(404, "Unknown feed")


@app.get("/api/supply/signals")
def supply_signals():
    return supply_risk.signals_view()


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


@app.get("/api/governance/config")
def gov_config():
    """Active governance rules, including those of registered entities and packs."""
    cfg = governance.config()
    return {k: cfg.get(k) for k in ("version", "weights", "grades", "confidence", "quarantine_sla_days", "drift_threshold_pct",
                                    "gold_target_pct", "domains", "required", "keys", "hard_keys", "enums", "ranges",
                                    "freshness", "products", "asset_id_pattern")}


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
    if action not in ("accept", "modify", "override", "clear"):
        raise HTTPException(404, "Actions: accept, modify, override, clear")
    try:
        if action == "clear":
            return governance.clear_decision(did, body.user, body.text)
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
