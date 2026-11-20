"""Sign-in, access requests, approval, module access, passcode resets, lockout and the admin console."""
import os

os.environ["MONGO_URI"] = "mongomock://"
os.environ["AI_ENABLED"] = "false"
os.environ["RISK_FEEDS_ENABLED"] = "false"

import pytest
from fastapi.testclient import TestClient

from app import auth
from app.db import get_db
from app.main import app

ADMIN_PW = "Admin-passcode-2026"


@pytest.fixture(autouse=True)
def on(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("MAESTRO_ADMIN_USER", "chief")
    monkeypatch.setenv("MAESTRO_ADMIN_PASSWORD", ADMIN_PW)
    db = get_db()
    for n in list(db.list_collection_names()):
        db[n].delete_many({})
    auth.ensure_bootstrap()


def client():
    return TestClient(app, follow_redirects=False)


def signed_in(user, pw):
    c = client()
    r = c.post("/api/auth/login", json={"username": user, "passcode": pw})
    assert r.status_code == 200, r.text
    return c


def test_everything_needs_sign_in():
    c = client()
    assert c.get("/api/supply/overview").status_code == 401
    r = c.get("/maestro")
    assert r.status_code in (302, 307) and r.headers["location"].startswith("/login?next=/maestro")
    assert c.get("/login").status_code == 200 and c.get("/api/health").status_code == 200


def test_admin_sign_in_logout_and_log():
    c = client()
    assert c.post("/api/auth/login", json={"username": "chief", "passcode": "wrong-one-123"}).status_code == 401
    c = signed_in("chief", ADMIN_PW)
    me = c.get("/api/auth/me").json()
    assert me["role"] == "ADMIN" and "admin" in me["modules"] and "supply" in me["modules"]
    assert c.get("/api/supply/overview").status_code == 200
    c.post("/api/auth/logout")
    assert c.get("/api/supply/overview").status_code == 401
    events = [(e["event"], e["ok"]) for e in get_db().auth_log.find().sort("at", 1)]
    assert events == [("sign_in", False), ("sign_in", True), ("sign_out", True)]


def test_request_access_approval_and_module_access():
    c = client()
    bad = c.post("/api/auth/request-access", json={"username": "ana", "email": "ana@example.com", "department": "Supply", "passcode": "short"})
    assert bad.status_code == 422 and "10 characters" in bad.json()["detail"]
    r = c.post("/api/auth/request-access", json={"username": "ana", "email": "ana@example.com", "department": "Supply", "passcode": "Supply-analyst-1"})
    assert r.json()["status"] == "PENDING"
    assert c.post("/api/auth/request-access", json={"username": "ana", "email": "x@example.com", "department": "x", "passcode": "Another-pass-22"}).status_code == 422
    r = c.post("/api/auth/login", json={"username": "ana", "passcode": "Supply-analyst-1"})
    assert r.status_code == 401 and "waiting" in r.json()["detail"]
    admin = signed_in("chief", ADMIN_PW)
    assert [u["username"] for u in admin.get("/api/admin/users").json() if u["status"] == "PENDING"] == ["ana"]
    assert admin.post("/api/admin/users/ana/approve", json={"groups": ["Nope"]}).status_code == 422
    assert admin.post("/api/admin/users/ana/approve", json={"groups": ["Supply analysts"]}).status_code == 200
    ana = signed_in("ana", "Supply-analyst-1")
    assert ana.get("/api/auth/me").json()["modules"] == ["supply", "ai"]
    assert ana.get("/api/supply/overview").status_code == 200
    assert ana.get("/api/governance/overview").status_code == 403
    assert ana.get("/api/batches").status_code == 403 and ana.get("/api/admin/users").status_code == 403
    assert ana.get("/api/maestro/alerts").status_code == 200        # the alert bar works for everyone
    r = ana.get("/governance")
    assert r.status_code in (302, 307) and "denied=governance" in r.headers["location"]
    # the audit log records the signed-in account next to the typed name
    ana.post("/api/supply/decisions", json={"decision": "ACCEPT", "user": "typed name", "subject": "x"})
    a = get_db().audit.find_one({"action": "risk_decision"})
    assert a["user"] == "typed name" and a["account"] == "ana"


def test_reset_passcode_with_admin_issued_code():
    admin = signed_in("chief", ADMIN_PW)
    client().post("/api/auth/request-access", json={"username": "bob", "email": "bob@example.com", "department": "Depot", "passcode": "Depot-planner-77"})
    admin.post("/api/admin/users/bob/approve", json={"groups": ["Depot / AMXG"]})
    bo = signed_in("bob", "Depot-planner-77")
    c = client()
    same = c.post("/api/auth/reset-request", json={"username": "bob", "email": "wrong@example.com"}).json()
    assert same == c.post("/api/auth/reset-request", json={"username": "bob", "email": "bob@example.com"}).json()  # no account discovery
    assert [r["username"] for r in admin.get("/api/admin/reset-requests").json()] == ["bob"]
    code = admin.post("/api/admin/users/bob/reset-code").json()["code"]
    assert c.post("/api/auth/reset", json={"username": "bob", "code": "AAAA-BBBB-CCCC", "passcode": "New-passcode-88"}).status_code == 401
    assert c.post("/api/auth/reset", json={"username": "bob", "code": code.lower(), "passcode": "New-passcode-88"}).status_code == 200
    assert bo.get("/api/amxg/hub").status_code == 401                    # signed out everywhere
    assert c.post("/api/auth/reset", json={"username": "bob", "code": code, "passcode": "Other-passcode-99"}).status_code == 401  # once
    assert c.post("/api/auth/login", json={"username": "bob", "passcode": "Depot-planner-77"}).status_code == 401
    signed_in("bob", "New-passcode-88")


def test_lockout_disable_and_admin_safeguards():
    c = client()
    for _ in range(5):
        c.post("/api/auth/login", json={"username": "chief", "passcode": "wrong-one-123"})
    r = c.post("/api/auth/login", json={"username": "chief", "passcode": ADMIN_PW})
    assert r.status_code == 401 and "Too many" in r.json()["detail"]
    get_db().users.update_one({"_id": "chief"}, {"$set": {"locked_until": None}})
    admin = signed_in("chief", ADMIN_PW)
    assert "remove your own" in admin.patch("/api/admin/users/chief", json={"role": "USER"}).json()["detail"]
    client().post("/api/auth/request-access", json={"username": "cyd", "email": "cyd@example.com", "department": "IT", "passcode": "Viewer-account-5"})
    admin.post("/api/admin/users/cyd/approve", json={"groups": ["Viewers"]})
    cy = signed_in("cyd", "Viewer-account-5")
    assert admin.patch("/api/admin/users/cyd", json={"status": "DISABLED"}).status_code == 200
    assert cy.get("/api/supply/overview").status_code == 401
    # groups: create, give a module, delete only when empty
    assert admin.put("/api/admin/groups/Auditors", json={"modules": ["governance"]}).json()["modules"] == ["governance"]
    assert admin.put("/api/admin/groups/Bad", json={"modules": ["nope"]}).status_code == 422
    assert admin.delete("/api/admin/groups/Viewers").status_code == 422
    assert admin.delete("/api/admin/groups/Auditors").status_code == 200


def test_change_passcode_and_generated_first_admin(monkeypatch):
    admin = signed_in("chief", ADMIN_PW)
    assert admin.post("/api/auth/change-passcode", json={"current": "nope", "passcode": "Brand-new-pass-1"}).status_code == 401
    assert admin.post("/api/auth/change-passcode", json={"current": ADMIN_PW, "passcode": "Brand-new-pass-1"}).status_code == 200
    signed_in("chief", "Brand-new-pass-1")
    # without a configured passcode the first admin gets a random one and must change it
    get_db().users.delete_many({})
    monkeypatch.delenv("MAESTRO_ADMIN_PASSWORD")
    auth.ensure_bootstrap()
    u = get_db().users.find_one({"_id": "chief"})
    assert u["must_change"] and u["role"] == "ADMIN"
