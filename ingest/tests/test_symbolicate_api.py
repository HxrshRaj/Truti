import json

from conftest import ADMIN, make_event
from truti_ingest.symbolicate import SourceMapIndex, _vlq, symbolicate_frames

B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"


def enc(n):
    v = (-n << 1) | 1 if n < 0 else n << 1
    out = ""
    while True:
        d, v = v & 31, v >> 5
        out += B64[d | (32 if v else 0)]
        if not v:
            return out


SRC = "\n".join(f"// line {i}" for i in range(1, 5)) + "\nfunction add(a,b){\n  return a+b.x;\n}\n"


def make_map():
    # generated line 1: seg A at col 0 -> src0 line 5 (idx 4) col 0 name0 ; seg B at col 20 -> line 6 col 2
    seg_a = enc(0) + enc(0) + enc(4) + enc(0) + enc(0)
    seg_b = enc(20) + enc(0) + enc(1) + enc(2)
    return json.dumps({"version": 3, "file": "app.min.js", "sources": ["src/app.js"], "sourcesContent": [SRC],
                       "names": ["add"], "mappings": f"{seg_a},{seg_b};"})


def test_vlq_roundtrip():
    for n in (0, 1, -1, 15, 16, -16, 1000, -123456):
        assert _vlq(enc(n)) == [n]


def test_lookup_greatest_lower_bound():
    idx = SourceMapIndex(make_map())
    a = idx.lookup(1, 5)
    assert (a["source"], a["line"], a["col"], a["name"]) == ("src/app.js", 5, 0, "add")
    b = idx.lookup(1, 25)
    assert (b["line"], b["col"], b["name"]) == ("6", 2, None) or (b["line"], b["col"]) == (6, 2)
    assert idx.lookup(2, 0) is None and idx.lookup(0, 0) is None


def test_symbolicate_frames_adds_context():
    idx = SourceMapIndex(make_map())
    frames = [{"filename": "https://x.io/app.min.js", "function": "a", "lineno": 1, "colno": 26},
              {"filename": "https://x.io/other.js", "function": "z", "lineno": 1, "colno": 1}]
    out, n = symbolicate_frames(frames, lambda f: idx if f.endswith("app.min.js") else None)
    assert n == 1
    assert out[0]["filename"] == "src/app.js" and out[0]["lineno"] == 6
    assert out[0]["context_line"] == "  return a+b.x;"
    assert out[0]["pre_context"][-1] == "function add(a,b){"
    assert out[1]["filename"] == "https://x.io/other.js"  # untouched


def test_end_to_end_js_event_is_symbolicated(env):
    r = env.api("POST", f"/projects/{env.pid}/releases/9.9/sourcemaps",
                data={"filename": "https://cdn/app.min.js"}, files={"file": ("app.min.js.map", make_map())})
    assert r.status_code == 201
    ev = make_event(platform="javascript", release="9.9")
    ev["exception"] = {"type": "TypeError", "value": "x", "frames": [
        {"filename": "https://cdn/app.min.js?v=3", "function": "n", "lineno": 1, "colno": 26}]}
    env.send(ev)
    env.drain()
    d = env.api("GET", f"/projects/{env.pid}/issues").json()["items"][0]
    f = env.api("GET", f"/issues/{d['id']}").json()["latest_event"]["exception"]["frames"][0]
    assert f["filename"] == "src/app.js" and f["lineno"] == 6 and f["symbolicated"] is True
    assert "culprit" in d


def test_invalid_sourcemap_rejected(env):
    r = env.api("POST", f"/projects/{env.pid}/releases/1/sourcemaps",
                data={"filename": "a.js"}, files={"file": ("a.map", "{bad")})
    assert r.status_code == 422


def test_admin_auth_required(env):
    assert env.client.get("/api/0/projects").status_code == 401
    assert env.client.get("/api/0/projects", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert env.client.get("/api/0/projects", headers=ADMIN).status_code == 200


def test_project_create_returns_dsn(env):
    p = env.api("POST", "/projects", json={"name": "new"}).json()
    assert p["dsn"].startswith(f"http://{p['public_key']}@") and p["dsn"].endswith(f"/{p['id']}")
    assert env.api("POST", "/projects", json={"name": ""}).status_code == 422


def test_issue_list_filters_sort_and_sparkline(env):
    a = make_event()
    b = make_event(release="2.0", environment="staging")
    b["exception"] = {"type": "KeyError", "value": "k", "frames": [
        {"filename": "z.py", "function": "zed", "lineno": 1, "context_line": "d[k]", "in_app": True}]}
    env.send(a, make_event(), make_event(), b)
    env.drain()
    base = f"/projects/{env.pid}/issues"
    res = env.api("GET", base).json()
    assert res["total"] == 2 and res["items"][0]["count"] in (1, 3)
    assert len(res["items"][0]["sparkline"]) == 24 and sum(res["items"][0]["sparkline"]) == res["items"][0]["count"]
    assert env.api("GET", base, params={"sort": "count"}).json()["items"][0]["count"] == 3
    assert env.api("GET", base, params={"query": "keyerror"}).json()["total"] == 1
    assert env.api("GET", base, params={"environment": "staging"}).json()["items"][0]["type"] == "KeyError"
    assert env.api("GET", base, params={"release": "2.0"}).json()["total"] == 1
    rel = env.api("GET", f"/projects/{env.pid}/releases").json()
    assert {r["version"] for r in rel["releases"]} == {"1.0.0", "2.0"} and "prod" in rel["environments"]


def test_issue_detail_events_and_status(env):
    env.send(make_event(tags={"k": "v"}, user={"id": "7"}), make_event())
    env.drain()
    iid = env.api("GET", f"/projects/{env.pid}/issues").json()["items"][0]["id"]
    d = env.api("GET", f"/issues/{iid}").json()
    assert d["latest_event"]["exception"]["frames"][0]["context_line"]
    assert len(env.api("GET", f"/issues/{iid}/events").json()) == 2
    assert env.api("PATCH", f"/issues/{iid}", json={"status": "resolved"}).json()["status"] == "resolved"
    assert env.api("PATCH", f"/issues/{iid}", json={"status": "weird"}).status_code == 422
    env.send(make_event())  # regression reopens
    env.drain()
    assert env.api("GET", f"/issues/{iid}").json()["status"] == "unresolved"
    assert env.api("GET", "/issues/9999").status_code == 404


def test_alert_rule_crud(env):
    p = f"/projects/{env.pid}/alert-rules"
    rid = env.api("POST", p, json={"kind": "spike", "webhook_url": "http://h.test/x"}).json()["id"]
    assert len(env.api("GET", p).json()) == 1
    assert env.api("POST", p, json={"kind": "bogus", "webhook_url": "http://h.test/x"}).status_code == 422
    assert env.api("DELETE", f"/alert-rules/{rid}").status_code == 204
    assert env.api("GET", p).json() == []
