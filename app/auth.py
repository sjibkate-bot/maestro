"""Sign-in, access requests, passcode resets, groups and module access.

* Operators sign in with an operator ID and passcode. Sessions are random tokens in an
  HttpOnly cookie; only their SHA-256 is stored. Every sign-in attempt, success or not,
  and every sign-out is logged.
* Five failed attempts lock the account for 15 minutes.
* Anyone can request access (username, email, department, passcode). The account stays
  PENDING until an administrator approves it and assigns groups.
* Passcode reset: the user asks for a reset; an administrator issues a one-time code
  (valid 24 hours) and hands it over; the user sets a new passcode with it. There is no
  email server, so codes are never sent anywhere automatically.
* Groups grant modules. Module access is enforced on the server for pages and APIs.
* The first administrator comes from MAESTRO_ADMIN_USER / MAESTRO_ADMIN_PASSWORD. If no
  passcode is set, a random one is generated and written to the log once.
"""
import contextvars
import hashlib
import hmac
import logging
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from .db import get_db

log = logging.getLogger(__name__)

COOKIE = "maestro_session"
SESSION_HOURS = int(os.getenv("SESSION_HOURS", "12"))
LOCK_AFTER, LOCK_MINUTES = 5, 15
RESET_HOURS = 24

# module id -> label. "admin" is the admin console (administrators only).
MODULES = {
    "supply": "Supply analysis", "amxg": "AMXG", "depot": "Docks, gates & machine", "fleet": "Fleet health & alerts",
    "governance": "Data governance", "configuration": "Configuration (load, map, publish, delete)", "ai": "Ask Maestro (AI)",
}
DEFAULT_GROUPS = [
    ("Administrators", "Full access", list(MODULES)),
    ("Supply analysts", "Supply risk and what-if", ["supply", "ai"]),
    ("Depot / AMXG", "Depot flow, AMXG and fleet", ["amxg", "depot", "fleet", "ai"]),
    ("Data stewards", "Load, map, publish and govern data", ["configuration", "governance"]),
    ("Viewers", "Read every dashboard", ["supply", "amxg", "depot", "fleet", "governance"]),
]

CURRENT_USER: contextvars.ContextVar[Optional[dict]] = contextvars.ContextVar("current_user", default=None)
USERNAME = re.compile(r"^[A-Za-z0-9._-]{3,40}$")
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def enabled() -> bool:
    return os.getenv("AUTH_ENABLED", "true").lower() == "true"


def now() -> datetime:
    return datetime.now(timezone.utc)


def _naive(d):
    return d.replace(tzinfo=None) if isinstance(d, datetime) and d.tzinfo else d


# ------------------------------------------------------------------ passcodes

def hash_passcode(passcode: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(passcode.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return f"scrypt${salt.hex()}${dk.hex()}"


def check_passcode(passcode: str, stored: str) -> bool:
    try:
        _, salt, dk = stored.split("$")
        got = hashlib.scrypt(passcode.encode(), salt=bytes.fromhex(salt), n=2 ** 14, r=8, p=1, dklen=32)
        return hmac.compare_digest(got.hex(), dk)
    except Exception:
        return False


def passcode_problem(passcode: str) -> Optional[str]:
    if len(passcode or "") < 10:
        return "Passcode must be at least 10 characters"
    if not re.search(r"[A-Za-z]", passcode) or not re.search(r"\d", passcode):
        return "Passcode must contain letters and digits"
    return None


def _sha(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ------------------------------------------------------------------ bootstrap

def ensure_bootstrap() -> None:
    """Default groups, and a first administrator when there are no users."""
    db = get_db()
    for name, desc, mods in DEFAULT_GROUPS:
        if not db.groups.find_one({"_id": name}):
            db.groups.insert_one({"_id": name, "description": desc, "modules": mods, "created_at": now(), "builtin": True})
    if db.users.count_documents({}, limit=1):
        return
    user = os.getenv("MAESTRO_ADMIN_USER", "admin")
    pw = os.getenv("MAESTRO_ADMIN_PASSWORD")
    generated = not pw
    if generated:
        pw = secrets.token_urlsafe(12) + "7a"
    db.users.insert_one({"_id": user, "email": os.getenv("MAESTRO_ADMIN_EMAIL", ""), "department": "Administration",
                         "display_name": "Administrator", "passcode": hash_passcode(pw), "status": "ACTIVE", "role": "ADMIN",
                         "groups": ["Administrators"], "created_at": now(), "approved_by": "bootstrap",
                         "must_change": generated})
    if generated:
        log.warning("=" * 70)
        log.warning("Maestro first administrator created: user %r, passcode %r", user, pw)
        log.warning("Sign in and change it, or set MAESTRO_ADMIN_PASSWORD before first start.")
        log.warning("=" * 70)


# ------------------------------------------------------------------ sign-in log

def _log(event: str, username: Optional[str], ok: bool, request=None, detail: str = "") -> None:
    get_db().auth_log.insert_one({"at": now(), "event": event, "username": username, "ok": ok, "detail": detail,
                                  "ip": request.client.host if request is not None and request.client else None,
                                  "user_agent": (request.headers.get("user-agent", "")[:200] if request is not None else None)})


# ------------------------------------------------------------------ sessions

def login(username: str, passcode: str, request=None) -> tuple[str, dict]:
    db = get_db()
    username = (username or "").strip()
    u = db.users.find_one({"_id": username})
    generic = "Operator ID or passcode is not correct"
    if not u:
        _log("sign_in", username, False, request, "unknown user")
        raise PermissionError(generic)
    if u.get("locked_until") and _naive(u["locked_until"]) > _naive(now()):
        _log("sign_in", username, False, request, "locked")
        raise PermissionError("Too many failed attempts. Try again in 15 minutes or ask an administrator.")
    if not check_passcode(passcode or "", u.get("passcode", "")):
        fails = u.get("failed", 0) + 1
        upd = {"failed": fails}
        if fails >= LOCK_AFTER:
            upd.update({"locked_until": now() + timedelta(minutes=LOCK_MINUTES), "failed": 0})
        db.users.update_one({"_id": username}, {"$set": upd})
        _log("sign_in", username, False, request, "wrong passcode" + (" - locked" if fails >= LOCK_AFTER else ""))
        raise PermissionError(generic)
    if u["status"] == "PENDING":
        _log("sign_in", username, False, request, "pending approval")
        raise PermissionError("Your access request is waiting for an administrator's approval.")
    if u["status"] != "ACTIVE":
        _log("sign_in", username, False, request, f"account {u['status'].lower()}")
        raise PermissionError("This account is not active. Contact an administrator.")
    token = secrets.token_urlsafe(32)
    db.sessions.insert_one({"_id": _sha(token), "username": username, "created_at": now(),
                            "expires_at": now() + timedelta(hours=SESSION_HOURS),
                            "ip": request.client.host if request is not None and request.client else None})
    db.users.update_one({"_id": username}, {"$set": {"failed": 0, "locked_until": None, "last_login": now()}})
    _log("sign_in", username, True, request)
    return token, me(username)


def logout(token: Optional[str], request=None) -> None:
    if not token:
        return
    s = get_db().sessions.find_one_and_delete({"_id": _sha(token)})
    if s:
        _log("sign_out", s["username"], True, request)


def user_for_token(token: Optional[str]) -> Optional[dict]:
    if not token:
        return None
    db = get_db()
    s = db.sessions.find_one({"_id": _sha(token)})
    if not s or _naive(s["expires_at"]) < _naive(now()):
        return None
    u = db.users.find_one({"_id": s["username"]})
    if not u or u["status"] != "ACTIVE":
        return None
    return me(u["_id"], u)


def modules_for(u: dict) -> list[str]:
    if u.get("role") == "ADMIN":
        return list(MODULES) + ["admin"]
    mods = set()
    for g in get_db().groups.find({"_id": {"$in": u.get("groups", [])}}):
        mods |= set(g.get("modules", []))
    return [m for m in MODULES if m in mods]


def me(username: str, u: Optional[dict] = None) -> dict:
    u = u or get_db().users.find_one({"_id": username})
    return {"username": u["_id"], "display_name": u.get("display_name") or u["_id"], "email": u.get("email"),
            "department": u.get("department"), "role": u.get("role", "USER"), "groups": u.get("groups", []),
            "modules": modules_for(u), "must_change": bool(u.get("must_change")), "last_login": u.get("last_login")}


def change_passcode(username: str, old: str, new: str) -> None:
    db = get_db()
    u = db.users.find_one({"_id": username})
    if not u or not check_passcode(old or "", u["passcode"]):
        raise PermissionError("Current passcode is not correct")
    p = passcode_problem(new)
    if p:
        raise ValueError(p)
    db.users.update_one({"_id": username}, {"$set": {"passcode": hash_passcode(new), "must_change": False}})
    _log("passcode_changed", username, True)


# ------------------------------------------------------------------ access requests and resets

def request_access(username: str, email: str, department: str, passcode: str, request=None) -> dict:
    db = get_db()
    username, email, department = (username or "").strip(), (email or "").strip(), (department or "").strip()
    if not USERNAME.match(username):
        raise ValueError("Username: 3-40 letters, digits, dot, dash or underscore")
    if not EMAIL.match(email):
        raise ValueError("Enter a valid email address")
    if not department:
        raise ValueError("Department is required")
    p = passcode_problem(passcode)
    if p:
        raise ValueError(p)
    if db.users.find_one({"_id": username}):
        raise ValueError("That username is taken or already requested")
    db.users.insert_one({"_id": username, "email": email, "department": department, "display_name": username,
                         "passcode": hash_passcode(passcode), "status": "PENDING", "role": "USER", "groups": [],
                         "created_at": now(), "requested_at": now()})
    _log("access_requested", username, True, request, department)
    return {"username": username, "status": "PENDING"}


def request_reset(username: str, email: str, request=None) -> dict:
    """Always answers the same way, so it cannot be used to discover accounts."""
    db = get_db()
    u = db.users.find_one({"_id": (username or "").strip()})
    ok = bool(u and (u.get("email") or "").lower() == (email or "").strip().lower() and u["status"] == "ACTIVE")
    if ok:
        db.reset_requests.update_one({"_id": u["_id"]}, {"$set": {"requested_at": now(), "status": "REQUESTED", "code_hash": None}},
                                     upsert=True)
    _log("reset_requested", (username or "").strip(), ok, request, "" if ok else "no matching active account")
    return {"status": "RECEIVED", "message": "If the details match an active account, an administrator will give you a reset code."}


def issue_reset_code(username: str, admin: str) -> dict:
    db = get_db()
    u = db.users.find_one({"_id": username})
    if not u:
        raise KeyError(username)
    code = "-".join(secrets.token_hex(2).upper() for _ in range(3))
    db.reset_requests.update_one({"_id": username}, {"$set": {"code_hash": _sha(code), "issued_by": admin, "issued_at": now(),
                                                              "expires_at": now() + timedelta(hours=RESET_HOURS), "status": "ISSUED"}},
                                 upsert=True)
    _log("reset_code_issued", username, True, None, f"by {admin}")
    return {"username": username, "code": code, "expires_in_hours": RESET_HOURS}


def reset_passcode(username: str, code: str, new: str, request=None) -> None:
    db = get_db()
    r = db.reset_requests.find_one({"_id": (username or "").strip()})
    if not r or not r.get("code_hash") or r.get("status") != "ISSUED" or _naive(r["expires_at"]) < _naive(now()) \
            or not hmac.compare_digest(r["code_hash"], _sha((code or "").strip().upper())):
        _log("passcode_reset", username, False, request, "invalid or expired code")
        raise PermissionError("Reset code is not valid or has expired")
    p = passcode_problem(new)
    if p:
        raise ValueError(p)
    db.users.update_one({"_id": r["_id"]}, {"$set": {"passcode": hash_passcode(new), "failed": 0, "locked_until": None,
                                                    "must_change": False}})
    db.reset_requests.update_one({"_id": r["_id"]}, {"$set": {"status": "USED", "used_at": now(), "code_hash": None}})
    db.sessions.delete_many({"username": r["_id"]})      # sign out everywhere
    _log("passcode_reset", r["_id"], True, request)


# ------------------------------------------------------------------ administration

def _admin_audit(action: str, admin: str, detail: dict) -> None:
    from . import governance
    governance.audit(action, admin, detail)


def users() -> list[dict]:
    out = []
    for u in get_db().users.find({}, {"passcode": 0}).sort("created_at", -1):
        u["username"] = u.pop("_id")
        out.append(u)
    return out


def approve(username: str, groups: list[str], role: str, admin: str) -> dict:
    db = get_db()
    u = db.users.find_one({"_id": username})
    if not u:
        raise KeyError(username)
    _check_groups(groups)
    if role not in ("USER", "ADMIN"):
        raise ValueError("Role must be USER or ADMIN")
    db.users.update_one({"_id": username}, {"$set": {"status": "ACTIVE", "groups": groups, "role": role,
                                                    "approved_by": admin, "approved_at": now()}})
    _admin_audit("user_approved", admin, {"user": username, "groups": groups, "role": role})
    return me(username)


def reject(username: str, admin: str, reason: str = "") -> dict:
    db = get_db()
    if not db.users.find_one({"_id": username}):
        raise KeyError(username)
    db.users.update_one({"_id": username}, {"$set": {"status": "REJECTED", "rejected_by": admin, "rejected_at": now(), "reason": reason}})
    _admin_audit("user_rejected", admin, {"user": username, "reason": reason})
    return {"username": username, "status": "REJECTED"}


def update_user(username: str, changes: dict, admin: str) -> dict:
    db = get_db()
    u = db.users.find_one({"_id": username})
    if not u:
        raise KeyError(username)
    upd = {}
    if "groups" in changes:
        _check_groups(changes["groups"])
        upd["groups"] = changes["groups"]
    if "role" in changes:
        if changes["role"] not in ("USER", "ADMIN"):
            raise ValueError("Role must be USER or ADMIN")
        upd["role"] = changes["role"]
    if "status" in changes:
        if changes["status"] not in ("ACTIVE", "DISABLED"):
            raise ValueError("Status must be ACTIVE or DISABLED")
        upd["status"] = changes["status"]
    if "display_name" in changes:
        upd["display_name"] = str(changes["display_name"])[:80]
    if username == admin and (upd.get("role") == "USER" or upd.get("status") == "DISABLED"):
        raise ValueError("You cannot remove your own administrator access")
    if (upd.get("role") == "USER" or upd.get("status") == "DISABLED") and u.get("role") == "ADMIN":
        if db.users.count_documents({"role": "ADMIN", "status": "ACTIVE", "_id": {"$ne": username}}) == 0:
            raise ValueError("At least one active administrator must remain")
    db.users.update_one({"_id": username}, {"$set": upd})
    if upd.get("status") == "DISABLED":
        db.sessions.delete_many({"username": username})
    _admin_audit("user_updated", admin, {"user": username, **upd})
    return me(username)


def _check_groups(groups: list[str]) -> None:
    known = {g["_id"] for g in get_db().groups.find({}, {"_id": 1})}
    bad = [g for g in groups or [] if g not in known]
    if bad:
        raise ValueError("Unknown group(s): " + ", ".join(bad))


def groups() -> list[dict]:
    db = get_db()
    out = []
    for g in db.groups.find().sort("_id", 1):
        g["name"] = g.pop("_id")
        g["members"] = db.users.count_documents({"groups": g["name"]})
        out.append(g)
    return out


def save_group(name: str, description: str, modules: list[str], admin: str) -> dict:
    name = (name or "").strip()
    if not name or len(name) > 60:
        raise ValueError("Group name is required (60 characters at most)")
    bad = [m for m in modules or [] if m not in MODULES]
    if bad:
        raise ValueError("Unknown module(s): " + ", ".join(bad))
    get_db().groups.update_one({"_id": name}, {"$set": {"description": description or "", "modules": list(modules or []),
                                                        "updated_at": now(), "updated_by": admin},
                                               "$setOnInsert": {"created_at": now()}}, upsert=True)
    _admin_audit("group_saved", admin, {"group": name, "modules": modules})
    return next(g for g in groups() if g["name"] == name)


def delete_group(name: str, admin: str) -> None:
    db = get_db()
    if db.users.count_documents({"groups": name}):
        raise ValueError("Remove the group from its members first")
    if not db.groups.delete_one({"_id": name}).deleted_count:
        raise KeyError(name)
    _admin_audit("group_deleted", admin, {"group": name})


def reset_requests() -> list[dict]:
    out = []
    for r in get_db().reset_requests.find({"status": {"$in": ["REQUESTED", "ISSUED"]}}, {"code_hash": 0}).sort("requested_at", -1):
        r["username"] = r.pop("_id")
        out.append(r)
    return out


def sign_in_log(limit: int = 200) -> list[dict]:
    return list(get_db().auth_log.find({}, {"_id": 0}).sort("at", -1).limit(limit))


# ------------------------------------------------------------------ module guard

OPEN_PATHS = ("/login", "/request-access", "/reset-passcode", "/api/auth/", "/api/health", "/favicon")
API_MODULE = [  # (prefix, module, methods or None for all)
    ("/api/admin", "admin", None), ("/api/alerts", None, None), ("/api/supply", "supply", None), ("/api/amxg", "amxg", None),
    ("/api/maestro/fleet", "fleet", None), ("/api/maestro/alerts", None, None), ("/api/maestro/modules", None, None), ("/api/maestro", "depot", None),
    ("/api/governance", "governance", None), ("/api/ask", "ai", None), ("/api/ingest", "configuration", None),
    ("/api/batches", "configuration", None), ("/api/objects", "configuration", None), ("/api/profiles", "configuration", None),
    ("/api/data", "configuration", None), ("/api/feeds", "configuration", None), ("/api/packs", "configuration", None),
    ("/api/mappings", "configuration", None), ("/api/sources", "configuration", None),
    ("/api/entities", "configuration", {"POST", "PUT", "PATCH", "DELETE"}),
]
PAGE_MODULE = {"/supply": "supply", "/governance": "governance"}


def required_module(path: str, method: str) -> Optional[str]:
    if path in PAGE_MODULE:
        return PAGE_MODULE[path]
    for prefix, module, methods in API_MODULE:
        if path.startswith(prefix) and (methods is None or method in methods):
            return module
    return None
