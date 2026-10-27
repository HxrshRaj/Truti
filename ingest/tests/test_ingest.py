from conftest import Env, envelope, make_event


def test_accepts_valid_envelope_and_queues(env):
    r = env.send(make_event(), make_event())
    assert r.status_code == 202 and r.json() == {"accepted": 2, "duplicates": 0}
    assert env.sync_redis.xlen(env.settings.stream) == 2


def test_key_via_query_param(env):
    r = env.client.post(f"/api/{env.pid}/envelope/?truti_key={env.key}", json=envelope(make_event()))
    assert r.status_code == 202


def test_bad_key_and_unknown_project(env):
    assert env.send(make_event(), key="nope").status_code == 401
    r = env.client.post("/api/999/envelope/", json=envelope(make_event()), headers={"X-Truti-Auth": "truti_key=x"})
    assert r.status_code == 401
    assert env.sync_redis.xlen(env.settings.stream) == 0


def test_invalid_json_400(env):
    r = env.client.post(f"/api/{env.pid}/envelope/", content=b"{nope",
                        headers={"X-Truti-Auth": f"truti_key={env.key}"})
    assert r.status_code == 400 and "JSON" in r.json()["error"]


def test_schema_errors_422_with_details(env):
    r = env.send(make_event(event_id="short"), make_event(platform="cobol"))
    assert r.status_code == 422
    locs = [d["loc"] for d in r.json()["details"]]
    assert any("event_id" in l for l in locs) and any("platform" in l for l in locs)


def test_event_needs_exception_or_message(env):
    ev = make_event()
    del ev["exception"]
    assert env.send(ev).status_code == 422
    ev["message"] = "hello"
    assert env.send(ev).status_code == 202


def test_wrong_version_and_empty_and_too_many(env):
    c, h = env.client, {"X-Truti-Auth": f"truti_key={env.key}"}
    u = f"/api/{env.pid}/envelope/"
    assert c.post(u, json={"envelope_version": 2, "events": [make_event()]}, headers=h).status_code == 422
    assert c.post(u, json=envelope(), headers=h).status_code == 422
    assert c.post(u, json=envelope(*[make_event() for _ in range(51)]), headers=h).status_code == 422


def test_body_size_limit_413():
    e = Env(max_body_bytes=500)
    r = e.send(make_event(message="x" * 2000))
    assert r.status_code == 413


def test_rate_limit_429_retry_after():
    e = Env(default_rate_limit_eps=5)
    assert e.send(*[make_event() for _ in range(5)]).status_code == 202
    r = e.send(make_event())
    assert r.status_code == 429
    assert int(r.headers["Retry-After"]) >= 1
    assert e.sync_redis.xlen(e.settings.stream) == 5


def test_rate_limit_is_per_project():
    e = Env(default_rate_limit_eps=2)
    from truti_ingest.db import Project
    with e.Session() as s:
        p2 = Project(name="other"); s.add(p2); s.commit(); pid2, key2 = p2.id, p2.public_key
    assert e.send(make_event(), make_event()).status_code == 202
    assert e.send(make_event()).status_code == 429
    r = e.client.post(f"/api/{pid2}/envelope/", json=envelope(make_event()),
                      headers={"X-Truti-Auth": f"truti_key={key2}"})
    assert r.status_code == 202


def test_dedup_by_event_id(env):
    ev = make_event()
    assert env.send(ev).json() == {"accepted": 1, "duplicates": 0}
    assert env.send(ev, make_event()).json() == {"accepted": 1, "duplicates": 1}
    assert env.sync_redis.xlen(env.settings.stream) == 2


def test_queue_full_503():
    e = Env(stream_maxlen=2)
    assert e.send(make_event(), make_event()).status_code == 202
    r = e.send(make_event())
    assert r.status_code == 503 and "Retry-After" in r.headers


def test_metrics_endpoint(env):
    env.send(make_event())
    env.send(make_event(event_id="bad"))
    text = env.client.get("/metrics").text
    for name in ("truti_events_received_total", "truti_events_rejected_total", "truti_events_queued_total",
                 "truti_processing_seconds", "truti_queue_length"):
        assert name in text


def _gz(obj):
    import gzip, json
    return gzip.compress(json.dumps(obj).encode())


def test_gzip_envelope_accepted(env):
    r = env.client.post(f"/api/{env.pid}/envelope/", content=_gz(envelope(make_event())),
                        headers={"X-Truti-Auth": f"truti_key={env.key}", "Content-Encoding": "gzip"})
    assert r.status_code == 202


def test_gzip_bomb_rejected_413():
    e = Env(max_body_bytes=2000)
    body = _gz(envelope(make_event(message="a" * 500_000)))
    assert len(body) < 2000  # small on the wire, huge when expanded
    r = e.client.post(f"/api/{e.pid}/envelope/", content=body,
                      headers={"X-Truti-Auth": f"truti_key={e.key}", "Content-Encoding": "gzip"})
    assert r.status_code == 413


def test_bad_gzip_and_unknown_encoding(env):
    h = {"X-Truti-Auth": f"truti_key={env.key}"}
    assert env.client.post(f"/api/{env.pid}/envelope/", content=b"not gzip",
                           headers={**h, "Content-Encoding": "gzip"}).status_code == 400
    assert env.client.post(f"/api/{env.pid}/envelope/", content=b"{}",
                           headers={**h, "Content-Encoding": "br"}).status_code == 415
