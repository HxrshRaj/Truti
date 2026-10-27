import os

import pytest
from sqlalchemy import create_engine, inspect

from conftest import Env, make_event


def reg(env, email="a@x.io", pw="password123"):
    return env.client.post("/api/0/auth/register", json={"email": email, "password": pw})


def hdr(tok):
    return {"Authorization": f"Bearer {tok}"}


def test_first_user_is_admin_then_registration_closes(env):
    r = reg(env)
    assert r.status_code == 201 and r.json()["user"]["is_admin"] is True
    assert reg(env, "b@x.io").status_code == 403
    assert reg(env).status_code in (403, 409)


def test_registration_open_via_setting():
    e = Env(allow_registration=True)
    assert reg(e).status_code == 201
    assert reg(e, "b@x.io").status_code == 201
    assert reg(e, "b@x.io").status_code == 409
    assert e.client.post("/api/0/auth/register", json={"email": "bad", "password": "password123"}).status_code == 422
    assert e.client.post("/api/0/auth/register", json={"email": "c@x.io", "password": "short"}).status_code == 422


def test_login_logout_and_me():
    e = Env(allow_registration=True)
    tok = reg(e).json()["token"]
    assert e.client.get("/api/0/auth/me", headers=hdr(tok)).json()["user"]["email"] == "a@x.io"
    assert e.client.post("/api/0/auth/login", json={"email": "a@x.io", "password": "wrongwrong"}).status_code == 401
    t2 = e.client.post("/api/0/auth/login", json={"email": "A@X.io", "password": "password123"}).json()["token"]
    assert e.client.post("/api/0/auth/logout", headers=hdr(t2)).status_code == 204
    assert e.client.get("/api/0/auth/me", headers=hdr(t2)).status_code == 401
    assert e.client.get("/api/0/auth/me", headers=hdr(tok)).status_code == 200


def test_login_throttle():
    e = Env(allow_registration=True)
    reg(e)
    codes = [e.client.post("/api/0/auth/login", json={"email": "a@x.io", "password": "wrongwrong"}).status_code
             for _ in range(10)]
    assert codes[:8] == [401] * 8 and codes[8] == 429
    # even the right password is refused while throttled
    assert e.client.post("/api/0/auth/login", json={"email": "a@x.io", "password": "password123"}).status_code == 429


def test_project_isolation_between_users():
    e = Env(allow_registration=True)
    alice = reg(e, "alice@x.io").json()["token"]
    bob = reg(e, "bob@x.io").json()["token"]
    p = e.client.post("/api/0/projects", json={"name": "alices"}, headers=hdr(alice)).json()
    # bob cannot see, read or write alice's project
    assert e.client.get("/api/0/projects", headers=hdr(bob)).json() == []
    assert e.client.get(f"/api/0/projects/{p['id']}/issues", headers=hdr(bob)).status_code == 404
    assert e.client.post(f"/api/0/projects/{p['id']}/alert-rules", headers=hdr(bob),
                         json={"kind": "new_issue", "webhook_url": "http://x.test"}).status_code == 404
    # events for alice's project; bob cannot read the issue by id either
    r = e.client.post(f"/api/{p['id']}/envelope/", json={"envelope_version": 1, "events": [make_event()]},
                      headers={"X-Truti-Auth": f"truti_key={p['public_key']}"})
    assert r.status_code == 202
    e.drain()
    iid = e.client.get(f"/api/0/projects/{p['id']}/issues", headers=hdr(alice)).json()["items"][0]["id"]
    assert e.client.get(f"/api/0/issues/{iid}", headers=hdr(bob)).status_code == 404
    assert e.client.patch(f"/api/0/issues/{iid}", json={"status": "resolved"}, headers=hdr(bob)).status_code == 404
    # alice invites bob as member: read ok, but cannot manage members
    assert e.client.post(f"/api/0/projects/{p['id']}/members", json={"email": "bob@x.io"}, headers=hdr(alice)).status_code == 201
    assert e.client.get(f"/api/0/issues/{iid}", headers=hdr(bob)).status_code == 200
    assert e.client.post(f"/api/0/projects/{p['id']}/members", json={"email": "alice@x.io", "role": "member"},
                         headers=hdr(bob)).status_code == 403
    assert {m["email"] for m in e.client.get(f"/api/0/projects/{p['id']}/members", headers=hdr(bob)).json()} == {"alice@x.io", "bob@x.io"}


def test_admin_token_sees_everything(env):
    assert env.api("GET", "/projects").status_code == 200
    assert env.client.get("/api/0/projects", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert env.client.get("/api/0/projects").status_code == 401


def test_merge_issues_moves_events_and_aliases_future_events(env):
    a = make_event()
    b = make_event()
    b["exception"] = {"type": "OtherError", "value": "x", "frames": [
        {"filename": "z.py", "function": "z", "lineno": 1, "context_line": "boom", "in_app": True}]}
    env.send(a, make_event(), b)
    env.drain()
    items = env.api("GET", f"/projects/{env.pid}/issues").json()["items"]
    by_type = {i["type"]: i["id"] for i in items}
    r = env.api("POST", f"/issues/{by_type['OtherError']}/merge", json={"into": by_type["ValueError"]})
    assert r.status_code == 200 and r.json()["count"] == 3
    assert env.api("GET", f"/issues/{by_type['OtherError']}").status_code == 404
    # the merged-away fingerprint keeps landing in the surviving issue
    b2 = make_event()
    b2["exception"] = b["exception"]
    env.send(b2)
    env.drain()
    res = env.api("GET", f"/projects/{env.pid}/issues").json()
    assert res["total"] == 1 and res["items"][0]["count"] == 4
    assert env.api("POST", f"/issues/{by_type['ValueError']}/merge", json={"into": by_type["ValueError"]}).status_code == 422


def test_migrations_build_the_same_schema_as_models(tmp_path):
    from alembic import command
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from truti_ingest.db import Base, make_session_factory
    url = f"sqlite:///{tmp_path}/m.db"
    make_session_factory(url, use_migrations=True)
    eng = create_engine(url)
    with eng.connect() as c:
        diff = compare_metadata(MigrationContext.configure(c, opts={"compare_type": True}), Base.metadata)
    assert diff == [], diff
    assert "alembic_version" in inspect(eng).get_table_names()
    make_session_factory(url, use_migrations=True)  # idempotent
