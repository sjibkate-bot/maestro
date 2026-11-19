"""Remote connectors: REST APIs, ERP systems (OData / REST) and IoT (HTTP push, MQTT).

ERP coverage: SAP S/4HANA and Microsoft Dynamics 365 expose OData; Oracle
Fusion, NetSuite, Odoo, Infor and most WMS/TMS expose REST/JSON. Both shapes
go through `pull_api`, which follows OData/REST paging and lets the AI map the
technical field names (MATNR, EBELN, ...) to the canonical schema.
"""
import json
import logging
import threading
from typing import Optional

import httpx

from ..pipeline import ingest_rows
from .files import find_records, flatten

log = logging.getLogger(__name__)
MAX_PAGES = 50


def _get_path(obj, path: Optional[str]):
    if not path:
        return find_records(obj)
    for part in path.split("."):
        obj = obj[int(part)] if isinstance(obj, list) else obj.get(part, [])
    return obj if isinstance(obj, list) else [obj]


def _next_link(payload, resp: httpx.Response) -> Optional[str]:
    if isinstance(payload, dict):
        for key in ("@odata.nextLink", "odata.nextLink", "next", "nextLink"):
            if isinstance(payload.get(key), str):
                return payload[key]
        d = payload.get("d")
        if isinstance(d, dict) and isinstance(d.get("__next"), str):  # OData v2 (SAP)
            return d["__next"]
    return resp.links.get("next", {}).get("url")


def pull_api(*, source_id: str, url: str, source_type: str = "api",
             headers: Optional[dict] = None, params: Optional[dict] = None,
             auth: Optional[tuple[str, str]] = None, records_path: Optional[str] = None,
             record_type_hint: Optional[str] = None, timeout: float = 60) -> dict:
    rows: list[dict] = []
    pages = 0
    with httpx.Client(headers=headers or {}, auth=auth, timeout=timeout, follow_redirects=True) as c:
        next_url, next_params = url, params
        while next_url and pages < MAX_PAGES:
            resp = c.get(next_url, params=next_params)
            resp.raise_for_status()
            payload = resp.json()
            rows.extend(flatten(_get_path(payload, records_path)))
            next_url, next_params = _next_link(payload, resp), None
            pages += 1
    return ingest_rows(rows, source_id=source_id, source_type=source_type,
                       record_type_hint=record_type_hint, meta={"url": url, "pages": pages})


def ingest_iot(source_id: str, payload) -> dict:
    """HTTP push from devices/gateways. Accepts one reading or a list."""
    readings = payload if isinstance(payload, list) else find_records(payload)
    return ingest_rows(flatten(readings), source_id=source_id, source_type="iot",
                       record_type_hint="sensor_reading", meta={"transport": "http"})


class MqttSubscriber:
    """Subscribes to an MQTT topic and ingests readings in small batches."""

    def __init__(self, source_id: str, host: str, topic: str, port: int = 1883,
                 username: Optional[str] = None, password: Optional[str] = None,
                 batch_size: int = 50):
        import paho.mqtt.client as mqtt

        self.source_id, self.topic, self.batch_size = source_id, topic, batch_size
        self.buffer: list[dict] = []
        self.lock = threading.Lock()
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        if username:
            self.client.username_pw_set(username, password)
        self.client.on_connect = lambda c, *_: c.subscribe(topic)
        self.client.on_message = self._on_message
        self.client.connect(host, port)

    def _on_message(self, client, userdata, msg):
        try:
            data = json.loads(msg.payload)
        except ValueError:
            data = {"value": msg.payload.decode(errors="replace")}
        items = data if isinstance(data, list) else [data]
        for it in items:
            it.setdefault("topic", msg.topic)
        with self.lock:
            self.buffer.extend(items)
            if len(self.buffer) >= self.batch_size:
                self.flush()

    def flush(self):
        batch, self.buffer = self.buffer, []
        if batch:
            ingest_rows(flatten(batch), source_id=self.source_id, source_type="iot",
                        record_type_hint="sensor_reading", meta={"transport": "mqtt"})

    def start(self):
        self.client.loop_start()

    def stop(self):
        self.client.loop_stop()
        with self.lock:
            self.flush()
