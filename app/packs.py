"""Industry packs: ready-made entity sets (fields, keys, synonyms, allowed values) that a
customer switches on, so their files map without registering every entity by hand.
Packs are JSON files in app/packs/; enabling one registers its entities in the registry."""
import json
from pathlib import Path

from .db import get_db
from .models import now

DIR = Path(__file__).resolve().parent / "packs"


def _load(pid: str) -> dict:
    f = DIR / f"{pid}.json"
    if not f.exists() or not pid.replace("_", "").isalnum():
        raise KeyError(pid)
    return json.loads(f.read_text())


def list_packs() -> list[dict]:
    db = get_db()
    enabled = {d["_id"]: d for d in db.packs.find()}
    out = []
    for f in sorted(DIR.glob("*.json")):
        p = json.loads(f.read_text())
        e = enabled.get(p["id"])
        out.append({"id": p["id"], "name": p["name"], "description": p["description"],
                    "entities": [{"name": x["name"], "label": x.get("label"), "fields": len(x["fields"])} for x in p["entities"]],
                    "enabled": bool(e), "enabled_by": e and e.get("user"), "enabled_at": e and e.get("at")})
    return out


def enable(pid: str, user: str) -> dict:
    from . import governance, registry
    if not (user or "").strip():
        raise ValueError("Say who is enabling the pack")
    p = _load(pid)
    db = get_db()
    done, skipped = [], []
    for e in p["entities"]:
        cur = registry.definition(e["name"])
        if cur and cur.get("pack") == pid and cur.get("fields") == e["fields"]:
            skipped.append(e["name"])
            continue
        if cur and cur.get("pack") != pid:
            raise ValueError(f"{e['name']} is already registered by {cur.get('pack') or cur.get('created_by')}")
        registry.create(e, user)
        done.append(e["name"])
    db.packs.update_one({"_id": pid}, {"$set": {"user": user, "at": now()}}, upsert=True)
    governance.audit("pack_enabled", user, {"pack": pid, "registered": done})
    return {"pack": pid, "registered": done, "unchanged": skipped}
