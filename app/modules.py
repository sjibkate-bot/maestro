"""Which data-driven Maestro modules to show.

AMXG, Docks / Gates / Machine and Fleet are built for particular kinds of data (aircraft in
depot gates, asset health). They appear when that data is loaded, so a car maker doesn't see
empty aircraft screens. An administrator can turn a module on or off regardless of the data.
Visibility is not access: who may open a module is still set by groups in the admin console.
"""
from .db import get_db
from .models import now

# module -> the entities whose records make it relevant
DATA = {
    "amxg": ["asset_flow", "gate_event", "gate_plan", "work_task", "production_summary", "issue"],
    "depot": ["asset_flow", "gate_event", "gate_plan"],
    "fleet": ["asset_status", "subsystem_health", "sensor_reading", "fault"],
}
LABELS = {"amxg": "AMXG", "depot": "Docks, gates & machine", "fleet": "Fleet health"}
MODES = ("auto", "on", "off")
LIVE_LAYERS = ["silver", "quarantine"]


def _settings() -> dict:
    return (get_db().settings.find_one({"_id": "module_visibility"}) or {}).get("modes", {})


def visibility() -> dict:
    db = get_db()
    modes = _settings()
    out = {}
    for m, ents in DATA.items():
        has = db.records.find_one({"record_type": {"$in": ents}, "layer": {"$in": LIVE_LAYERS}}, {"_id": 1}) is not None
        mode = modes.get(m, "auto")
        out[m] = {"label": LABELS[m], "mode": mode, "has_data": has, "entities": ents,
                  "visible": mode == "on" or (mode == "auto" and has)}
    return out


def set_modes(changes: dict, user: str) -> dict:
    from .governance import audit
    bad = [k for k, v in changes.items() if k not in DATA or v not in MODES]
    if bad:
        raise ValueError(f"Unknown module or mode: {', '.join(bad)} (modes: {', '.join(MODES)})")
    modes = {**_settings(), **changes}
    get_db().settings.update_one({"_id": "module_visibility"}, {"$set": {"modes": modes, "by": user, "at": now()}}, upsert=True)
    audit("module_visibility", user, changes)
    return visibility()
