"""Alert Center: one list of alerts for every industry, worked like a queue.

Alerts are computed from the governed data each time they are read, so they always match the
screens. What people do with an alert (acknowledge, assign, resolve) is kept by the alert's key
in `alert_state`, with an audit entry for every change. Sources:

  Supplier alert  alerts loaded from files (entity "alert"), not closed
  Stock           items whose days of cover are below the thresholds, or below reorder point
  Delivery        open purchase-order lines past their promised date, per supplier
  Supplier risk   suppliers rated at a level the admin chose (Critical by default), sanctions matches
  Risk feed       public-feed signals (hazards, unrest) for countries where suppliers sit
  Data quality    open quarantine by entity, drift, Gold products that went stale or need recertifying
  Fleet & depot   faults, telemetry breaches, AMREP and gate overruns (aircraft data)

Notifications (optional): new alerts at the chosen levels are posted to a Teams or Slack
incoming webhook (ALERT_WEBHOOK_URL) and/or emailed (SMTP_HOST, ALERT_EMAIL_TO, ...).
"""
import hashlib
import logging
import os
import smtplib
import threading
import time
from email.message import EmailMessage
from typing import Optional

from .db import get_db
from .models import now

log = logging.getLogger(__name__)

LEVELS = ("critical", "warning", "info")
STATUSES = ("NEW", "ACKNOWLEDGED", "RESOLVED")
CATEGORIES = {  # category -> module that may see it
    "Supplier alert": "supply", "Stock": "supply", "Delivery": "supply", "Supplier risk": "supply",
    "Risk feed": "supply", "Data quality": "governance", "Fleet & depot": "fleet",
}
DEFAULT_RULES = {
    "cover_critical_days": 7, "cover_warning_days": 14, "late_po_grace_days": 0,
    "supplier_levels": ["Critical"], "notify_levels": ["critical"],
    "categories": {c: True for c in CATEGORIES},
}


# ---------------------------------------------------------------- rules

def rules() -> dict:
    doc = get_db().settings.find_one({"_id": "alert_rules"}) or {}
    out = {**DEFAULT_RULES, **{k: v for k, v in doc.get("rules", {}).items() if k in DEFAULT_RULES}}
    out["categories"] = {**DEFAULT_RULES["categories"], **out.get("categories", {})}
    return out


def set_rules(changes: dict, user: str) -> dict:
    from .governance import audit
    bad = [k for k in changes if k not in DEFAULT_RULES]
    if bad:
        raise ValueError(f"Unknown alert rule: {', '.join(bad)}")
    cur = rules()
    new = {**cur, **changes}
    for k in ("cover_critical_days", "cover_warning_days", "late_po_grace_days"):
        try:
            new[k] = float(new[k])
        except (TypeError, ValueError):
            raise ValueError(f"{k} must be a number")
        if new[k] < 0:
            raise ValueError(f"{k} can't be negative")
    if new["cover_critical_days"] > new["cover_warning_days"]:
        raise ValueError("Critical cover must be no more than warning cover")
    if not set(new["supplier_levels"]) <= {"Critical", "High", "Moderate"}:
        raise ValueError("supplier_levels: choose from Critical, High, Moderate")
    if not set(new["notify_levels"]) <= set(LEVELS):
        raise ValueError("notify_levels: choose from critical, warning, info")
    if set(new["categories"]) - set(CATEGORIES) or not all(isinstance(v, bool) for v in new["categories"].values()):
        raise ValueError("categories: true or false for each known category")
    get_db().settings.update_one({"_id": "alert_rules"}, {"$set": {"rules": new, "by": user, "at": now()}}, upsert=True)
    audit("alert_rules", user, changes)
    return new


# ---------------------------------------------------------------- collecting

def _key(*parts) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:16]


def _a(key, level, category, subject, text, source, link=None, when=None) -> dict:
    return {"key": key, "level": level, "category": category, "subject": subject, "text": text,
            "source": source, "link": link, "when": when}


def _supply(r: dict) -> list[dict]:
    from urllib.parse import quote
    from . import supply_risk as sr
    out = []
    m = sr.build()
    sup_by_ref = m["suppliers"]
    name = lambda code: (sup_by_ref.get(m["ref"].get(sr._key(code), sr._key(code))) or {}).get("supplier") or code
    slink = lambda n: f"/supply#suppliers/{quote(n)}"
    cats = r["categories"]
    if cats["Supplier alert"]:
        sev = {"critical": "critical", "high": "warning", "medium": "warning", "low": "info"}
        for a in sr._gold("alert"):
            if (a.get("status") or "new").lower() in ("closed", "resolved"):
                continue
            who = name(a.get("node")) if a.get("node") else None
            out.append(_a(_key("file", a.get("alert_id") or a.get("text")), sev.get((a.get("alert_severity") or "").lower(), "warning"),
                          "Supplier alert", who, a.get("text") or "", a.get("source") or "Loaded alert",
                          slink(who) if who else "/supply#signals", a.get("ingested_at")))
    if cats["Stock"]:
        for it in m["items"].values():
            c = it.get("days_cover")
            desc = f" ({it['description']})" if it.get("description") else ""
            if c is not None and c < r["cover_critical_days"]:
                lvl = "critical"
            elif c is not None and c < r["cover_warning_days"]:
                lvl = "warning"
            elif it.get("below_reorder"):
                lvl = "info"
            else:
                continue
            text = (f"{c:.1f} days of cover left" if c is not None else "Below reorder point") + \
                   (f"; sole source {', '.join(it['suppliers'])}" if it.get("sole_sourced") and it.get("suppliers") else "") + \
                   (f"; runs out {it['stockout_date']:%d %b %Y}" if it.get("stockout_date") and hasattr(it["stockout_date"], "strftime") else "")
            out.append(_a(_key("cover", it["sku"]), lvl, "Stock", f"{it['sku']}{desc}", text, "Stock coverage", "/supply#coverage"))
    if cats["Delivery"]:
        today = sr._today()
        grace = r["late_po_grace_days"]
        late = {}
        for p in sr._gold("purchase_order"):
            pr = sr._naive(p.get("promised_date"))
            if pr and not p.get("delivered_date") and (today - pr).days > grace:
                s = late.setdefault(name(p.get("supplier")) if p.get("supplier") else "Unknown supplier", {"lines": 0, "worst": 0})
                s["lines"] += 1
                s["worst"] = max(s["worst"], (today - pr).days)
        for s, v in late.items():
            out.append(_a(_key("late", s), "critical" if v["worst"] > 14 else "warning", "Delivery", s,
                          f"{v['lines']} open order line(s) past promised date, worst {v['worst']} days late",
                          "Purchase orders", "/supply#delivery"))
    for s in m["suppliers"].values():
        if cats["Supplier risk"] and s["rating_class"]["label"] in r["supplier_levels"]:
            out.append(_a(_key("rating", s["supplier"]), "critical" if s["rating_class"]["label"] == "Critical" else "warning",
                          "Supplier risk", s["supplier"], f"Rated {s['rating_class']['label']} ({s['rating']}/25)",
                          "Supplier risk rating", slink(s["supplier"])))
        if cats["Supplier risk"] and s.get("sanctions_match"):
            out.append(_a(_key("sanctions", s["supplier"]), "critical", "Supplier risk", s["supplier"],
                          "Possible sanctions-list match: check before the next order", "Sanctions screening", slink(s["supplier"])))
    if cats["Risk feed"]:
        try:
            sv = sr.signals_view()
        except Exception:                      # feeds not loaded yet: no feed alerts
            sv = {"countries": []}
        for c in sv["countries"]:
            for sg in c.get("signals") or []:
                sev = sg.get("severity") or 0
                if sg.get("category") not in ("hazard", "unrest") or sev < 60:   # events, not standing indices
                    continue
                out.append(_a(_key("feed", c["iso3"], sg.get("title")), "critical" if sev >= 80 else "warning",
                              "Risk feed", f"{c['country']}: {', '.join(c['suppliers'][:3])}{'…' if len(c['suppliers']) > 3 else ''}",
                              sg.get("title") or "External signal", sg.get("source") or "Risk feed",
                              "/supply#signals", sg.get("event_date")))
    return out


def _governance(r: dict) -> list[dict]:
    from collections import Counter
    from . import governance
    if not r["categories"]["Data quality"]:
        return []
    out = []
    q = governance.quarantine("open")
    for rt, n in Counter(x["record_type"] for x in q).items():
        overdue = sum(1 for x in q if x["record_type"] == rt and x["overdue"])
        out.append(_a(_key("quarantine", rt), "warning" if overdue else "info", "Data quality", rt,
                      f"{n} record(s) in quarantine" + (f", {overdue} past the fix deadline" if overdue else ""),
                      "Governance", f"/governance#drill?layer=quarantine&record_type={rt}"))
    for d in get_db().drift.find({}, {"_id": 0}):
        out.append(_a(_key("drift", d["record_type"], d["field"]), "warning", "Data quality", f"{d['record_type']}.{d['field']}",
                      f"Average moved {d['change_pct']:+.0f}% from the previous loads", "Drift monitor", "/governance#monitor"))
    for p in governance.products():
        if p["freshness"] == "STALE":
            out.append(_a(_key("stale", p["id"]), "info", "Data quality", p["name"], "Data is older than its freshness target",
                          "Governance", f"/governance#drill?product={p['id']}"))
        elif p["certification"] in ("RECERTIFY", "BLOCKED"):
            out.append(_a(_key("cert", p["id"]), "warning" if p["certification"] == "BLOCKED" else "info", "Data quality", p["name"],
                          "Grade below B: blocked from certification" if p["certification"] == "BLOCKED" else "New data since certification: recertify",
                          "Governance", f"/governance#drill?product={p['id']}"))
    return out


def _fleet(r: dict) -> list[dict]:
    from urllib.parse import quote
    from . import pdm
    if not r["categories"]["Fleet & depot"]:
        return []
    return [_a(_key("fleet", i["source"], i["asset_id"], i["text"]), i["level"], "Fleet & depot", i["asset_id"], i["text"], i["source"],
               f"/maestro#fleet/{quote(str(i['asset_id']))}", i.get("when")) for i in pdm.alerts()["items"]]


_cache: dict = {}


def _version() -> tuple:
    """Changes whenever anything alerts are computed from changes (records, quarantine, drift, rules, feeds)."""
    db = get_db()
    last = db.records.find_one({}, {"ingested_at": 1}, sort=[("ingested_at", -1)]) or {}
    rule = db.settings.find_one({"_id": "alert_rules"}, {"at": 1}) or {}
    return (db.records.count_documents({}), str(last.get("ingested_at")), db.quarantine.count_documents({"status": "open"}),
            db.drift.count_documents({}), str(rule.get("at")), db.certifications.count_documents({}), int(time.time() // 300))


def collect() -> list[dict]:
    v = _version()
    if _cache.get("v") == v:
        return _cache["items"]
    items = _collect()
    _cache.update(v=v, items=items)
    return items


def _collect() -> list[dict]:
    r = rules()
    items = []
    for f in (_supply, _governance, _fleet):
        try:
            items += f(r)
        except Exception as e:                  # one broken source must not hide the others
            log.warning("alert source %s failed: %s", f.__name__, e)
    seen, out = set(), []
    for a in items:
        if a["key"] not in seen:
            seen.add(a["key"])
            out.append(a)
    return out


# ---------------------------------------------------------------- the queue

def _visible(cat: str, modules: Optional[list]) -> bool:
    if modules is None:
        return True
    need = CATEGORIES.get(cat)
    return need in modules or (need == "fleet" and "depot" in modules)


def listing(modules: Optional[list] = None, status: Optional[str] = None, level: Optional[str] = None,
            category: Optional[str] = None) -> dict:
    db = get_db()
    state = {s["_id"]: s for s in db.alert_state.find()}
    rank = {lv: i for i, lv in enumerate(LEVELS)}
    rows = []
    for a in collect():
        if not _visible(a["category"], modules):
            continue
        st = state.get(a["key"], {})
        s = st.get("status", "NEW")
        # a resolved alert that has got worse since comes back
        if s == "RESOLVED" and rank[a["level"]] < rank.get(st.get("level_at", "info"), 2):
            s = "NEW"
        rows.append({**a, "status": s, "owner": st.get("owner"), "due": st.get("due"), "note": st.get("note"),
                     "updated_by": st.get("by"), "updated_at": st.get("at"), "first_seen": st.get("first_seen")})
    open_rows = [x for x in rows if x["status"] != "RESOLVED"]
    counts = {"open": len(open_rows), "critical": sum(x["level"] == "critical" for x in open_rows),
              "warning": sum(x["level"] == "warning" for x in open_rows), "info": sum(x["level"] == "info" for x in open_rows),
              "new": sum(x["status"] == "NEW" for x in rows), "acknowledged": sum(x["status"] == "ACKNOWLEDGED" for x in rows),
              "resolved": sum(x["status"] == "RESOLVED" for x in rows),
              "by_category": {c: sum(1 for x in open_rows if x["category"] == c) for c in CATEGORIES}}
    if status:
        rows = [x for x in rows if x["status"] == status.upper()] if status != "open" else open_rows
    if level:
        rows = [x for x in rows if x["level"] == level]
    if category:
        rows = [x for x in rows if x["category"] == category]
    rows.sort(key=lambda x: (x["status"] == "RESOLVED", rank[x["level"]], x["category"], str(x["subject"])))
    return {"counts": counts, "items": rows}


def act(key: str, action: str, user: str, owner: str = "", due: str = "", note: str = "") -> dict:
    from .governance import audit
    if not (user or "").strip():
        raise ValueError("Say who is acting on the alert")
    a = next((x for x in collect() if x["key"] == key), None)
    if not a:
        raise KeyError(key)
    db = get_db()
    st = db.alert_state.find_one({"_id": key}) or {}
    upd = {"by": user, "at": now(), "level_at": a["level"], "subject": a["subject"], "category": a["category"]}
    if action == "acknowledge":
        upd["status"] = "ACKNOWLEDGED"
    elif action == "assign":
        if not owner.strip():
            raise ValueError("Name the owner")
        upd.update(status="ACKNOWLEDGED", owner=owner.strip(), due=due or None)
    elif action == "resolve":
        if not note.strip():
            raise ValueError("Say how it was resolved")
        upd.update(status="RESOLVED", note=note.strip())
    elif action == "reopen":
        upd.update(status="NEW", note=None)
    else:
        raise ValueError(f"Unknown action {action}")
    db.alert_state.update_one({"_id": key}, {"$set": upd, "$setOnInsert": {"first_seen": now()}}, upsert=True)
    audit(f"alert_{action}", user, {"alert": key, "category": a["category"], "subject": a["subject"], "text": a["text"],
                                    "owner": upd.get("owner"), "due": upd.get("due"), "note": note or None,
                                    "previous": st.get("status", "NEW")})
    return {**a, **{k: upd.get(k) for k in ("status", "owner", "due", "note")}}


# ---------------------------------------------------------------- notifications

def channels() -> dict:
    return {"webhook": bool(os.getenv("ALERT_WEBHOOK_URL")),
            "email": bool(os.getenv("SMTP_HOST") and os.getenv("ALERT_EMAIL_TO"))}


def _message(items: list[dict]) -> str:
    base = os.getenv("MAESTRO_PUBLIC_URL", "").rstrip("/")
    lines = [f"Maestro-SC: {len(items)} new alert(s)"]
    for a in items[:20]:
        lines.append(f"- [{a['level'].upper()}] {a['category']} · {a['subject']}: {a['text']}")
    if len(items) > 20:
        lines.append(f"…and {len(items) - 20} more")
    if base:
        lines.append(f"Open the Alert Center: {base}/maestro#alerts")
    return "\n".join(lines)


def _send(text: str) -> dict:
    import httpx
    sent = {}
    url = os.getenv("ALERT_WEBHOOK_URL")
    if url:
        try:   # Slack and Teams incoming webhooks both accept {"text": ...}
            r = httpx.post(url, json={"text": text}, timeout=15)
            sent["webhook"] = r.status_code < 300 or f"HTTP {r.status_code}"
        except Exception as e:
            sent["webhook"] = f"failed: {e}"
    if os.getenv("SMTP_HOST") and os.getenv("ALERT_EMAIL_TO"):
        try:
            msg = EmailMessage()
            msg["Subject"] = text.splitlines()[0]
            msg["From"] = os.getenv("ALERT_EMAIL_FROM", "maestro-sc@localhost")
            msg["To"] = os.getenv("ALERT_EMAIL_TO")
            msg.set_content(text)
            with smtplib.SMTP(os.getenv("SMTP_HOST"), int(os.getenv("SMTP_PORT", "587")), timeout=20) as s:
                if os.getenv("SMTP_STARTTLS", "true").lower() == "true":
                    s.starttls()
                if os.getenv("SMTP_USER"):
                    s.login(os.getenv("SMTP_USER"), os.getenv("SMTP_PASSWORD", ""))
                s.send_message(msg)
            sent["email"] = True
        except Exception as e:
            sent["email"] = f"failed: {e}"
    return sent


def notify(test: bool = False) -> dict:
    """Send alerts at the notify levels that nobody has been told about yet (or a test message)."""
    if not any(channels().values()):
        return {"sent": {}, "alerts": 0, "detail": "No channel configured: set ALERT_WEBHOOK_URL or SMTP_HOST + ALERT_EMAIL_TO"}
    if test:
        return {"sent": _send("Maestro-SC test alert: notifications are working."), "alerts": 0}
    db = get_db()
    lv = set(rules()["notify_levels"])
    told = {s["_id"] for s in db.alert_state.find({"notified_at": {"$ne": None}}, {"_id": 1})}
    new = [a for a in listing()["items"] if a["status"] == "NEW" and a["level"] in lv and a["key"] not in told]
    if not new:
        return {"sent": {}, "alerts": 0}
    sent = _send(_message(new))
    if any(v is True for v in sent.values()):
        for a in new:
            db.alert_state.update_one({"_id": a["key"]}, {"$set": {"notified_at": now()},
                                                          "$setOnInsert": {"status": "NEW", "first_seen": now()}}, upsert=True)
    return {"sent": sent, "alerts": len(new)}


_thread: Optional[threading.Thread] = None


def start_notifier() -> None:
    """Check for new alerts every ALERT_CHECK_MINUTES (default 15) when a channel is configured."""
    global _thread
    if not any(channels().values()) or (_thread and _thread.is_alive()):
        return
    every = max(1, int(os.getenv("ALERT_CHECK_MINUTES", "15"))) * 60

    def loop():
        time.sleep(60)
        while True:
            try:
                notify()
            except Exception as e:
                log.warning("alert notification failed: %s", e)
            time.sleep(every)

    _thread = threading.Thread(target=loop, name="alert-notifier", daemon=True)
    _thread.start()
