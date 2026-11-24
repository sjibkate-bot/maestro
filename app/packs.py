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
                    "governance_labels": sorted((p.get("governance") or {}).get("products", {})),
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


def apply_governance_labels(cfg: dict) -> dict:
    """Give the built-in data products and domains the wording of the enabled industry packs.

    A pack's optional "governance" block renames products ({"products": {id: {"name", "description"}}})
    and sets domain owners ({"domains": {domain: {"owner", "steward", "sensitivity"}}}).
    Packs apply in the order they were enabled, so the latest one wins."""
    enabled = sorted(get_db().packs.find(), key=lambda d: (d.get("at") is None, d.get("at") or 0))
    for e in enabled:
        try:
            gov = _load(e["_id"]).get("governance") or {}
        except (KeyError, ValueError):
            continue
        for p in cfg["products"]:
            label = gov.get("products", {}).get(p["id"])
            if label:
                p.update({k: v for k, v in label.items() if k in ("name", "description")})
        for dom, label in gov.get("domains", {}).items():
            if dom in cfg["domains"]:
                cfg["domains"][dom].update({k: v for k, v in label.items() if k in ("owner", "steward", "sensitivity")})
    return cfg
