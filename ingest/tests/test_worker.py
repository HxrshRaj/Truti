import datetime as dt
import json

from sqlalchemy import select

from conftest import make_event
from truti_ingest.db import AlertLog, Event, Issue, Release
from truti_ingest.grouping import fingerprint, normalise_message


def issues(env):
    with env.Session() as s:
        return s.scalars(select(Issue).order_by(Issue.id)).all()


def test_same_stack_same_type_groups_into_one_issue(env):
    env.send(make_event(), make_event(exception={**make_event()["exception"], "value": "bad 99"}))
    assert env.drain() == 2
    (i,) = issues(env)
    assert i.count == 2 and i.title.startswith("ValueError") and i.culprit.startswith("parse")


def test_different_type_or_stack_splits(env):
    base = make_event()
    other_type = make_event(exception={**base["exception"], "type": "KeyError"})
    other_stack = make_event()
    other_stack["exception"] = {**base["exception"], "frames": [
        {"filename": "app/other.py", "function": "x", "lineno": 1, "context_line": "boom()", "in_app": True}]}
    env.send(base, other_type, other_stack)
    env.drain()
    assert len(issues(env)) == 3


def test_line_number_changes_do_not_split(env):
    a = make_event()
    b = make_event()
    for f in b["exception"]["frames"]:
        f["lineno"] += 7
    assert fingerprint(a) == fingerprint(b)


def test_library_frames_ignored_in_fingerprint():
    a, b = make_event(), make_event()
    lib = {"filename": "site-packages/x.py", "function": "a", "lineno": 1, "context_line": "q", "in_app": False}
    a["exception"]["frames"].insert(0, lib)
    b["exception"]["frames"].insert(0, {**lib, "function": "b"})
    assert fingerprint(a) == fingerprint(b)


def test_message_events_group_after_normalising():
    assert normalise_message("user 123 failed 'abc'") == normalise_message("user 456 failed 'zzz'")
    a = make_event(message="user 1 failed"); del a["exception"]
    b = make_event(message="user 2 failed"); del b["exception"]
    assert fingerprint(a) == fingerprint(b)


def test_first_last_seen_and_release_tracking(env):
    old = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=2)).isoformat()
    new = dt.datetime.now(dt.timezone.utc).isoformat()
    env.send(make_event(timestamp=new, release="2.0"), make_event(timestamp=old, release="1.0"))
    env.drain()
    with env.Session() as s:
        (i,) = s.scalars(select(Issue)).all()
        assert i.first_seen < i.last_seen
        assert {r.version for r in s.scalars(select(Release))} == {"1.0", "2.0"}
        assert i.first_release == "2.0"  # processed first; last_release follows latest processed
        assert i.last_release == "1.0"


def test_redelivery_does_not_double_count(env):
    ev = make_event()
    env.send(ev)
    env.drain()
    # simulate at-least-once redelivery: same event again straight onto the stream
    env.sync_redis.xadd(env.settings.stream, {"project_id": env.pid, "received_at": "0", "data": json.dumps(ev)})
    env.drain()
    (i,) = issues(env)
    assert i.count == 1


def test_poison_message_goes_to_dead_letter(env):
    env.sync_redis.xadd(env.settings.stream, {"project_id": "1", "received_at": "0", "data": "{not json"})
    env.send(make_event())
    env.drain()
    assert env.sync_redis.xlen(env.settings.dead_stream) == 1
    assert env.sync_redis.xpending(env.settings.stream, env.settings.group)["pending"] == 0
    assert len(issues(env)) == 1  # the good event next to it was still stored


def test_failed_batch_falls_back_to_per_event(env, monkeypatch):
    from truti_ingest import processing
    real = processing.process_batch
    calls = {"n": 0}

    def flaky(session, items):
        calls["n"] += 1
        raise RuntimeError("batch path broke")
    monkeypatch.setattr("truti_ingest.worker.process_batch", flaky)
    env.send(make_event(), make_event())
    env.drain()
    (i,) = issues(env)
    assert calls["n"] >= 1 and i.count == 2
    assert env.sync_redis.xpending(env.settings.stream, env.settings.group)["pending"] == 0


def test_batch_counts_many_events_with_few_issues(env):
    evs = [make_event() for _ in range(40)]
    for i in range(0, 40, 10):
        env.send(*evs[i:i + 10])
    env.drain()
    (i,) = issues(env)
    assert i.count == 40
    with env.Session() as s:
        assert s.scalar(select(Release.event_count)) == 40
        assert len(s.scalars(select(Event)).all()) == 40


def test_duplicate_inside_one_batch_counted_once(env):
    ev = make_event()
    for _ in range(2):
        env.sync_redis.xadd(env.settings.stream, {"project_id": env.pid, "received_at": "0", "data": json.dumps(ev)})
    env.drain()
    (i,) = issues(env)
    assert i.count == 1


def test_retention_prunes_old_events_and_empty_issues(env):
    from truti_ingest.worker import prune
    old = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=45)).isoformat()
    stale = make_event(timestamp=old)
    stale["exception"] = {"type": "OldError", "value": "x", "frames": [
        {"filename": "old.py", "function": "f", "lineno": 1, "context_line": "x", "in_app": True}]}
    env.send(stale, make_event())
    env.drain()
    assert len(issues(env)) == 2
    assert prune(env.Session, 0) == (0, 0)  # disabled
    ev, iss = prune(env.Session, 30)
    assert (ev, iss) == (1, 1)
    (left,) = issues(env)
    assert left.title.startswith("ValueError")


def test_new_issue_alert_webhook(env):
    env.api("POST", f"/projects/{env.pid}/alert-rules", json={"kind": "new_issue", "webhook_url": "http://hook.test/x"})
    env.send(make_event())
    env.send(make_event())
    env.drain()
    assert len(env.posted) == 1 and env.posted[0][1]["kind"] == "new_issue"
    assert env.posted[0][1]["issue"]["title"].startswith("ValueError")


def test_spike_alert_fires_once_per_window(env):
    env.api("POST", f"/projects/{env.pid}/alert-rules",
            json={"kind": "spike", "threshold": 3, "window_seconds": 60, "webhook_url": "http://hook.test/s"})
    for _ in range(5):
        env.send(make_event())
    env.drain()
    assert len(env.posted) == 1 and env.posted[0][1]["kind"] == "spike"
    log = env.api("GET", f"/projects/{env.pid}/alerts").json()
    assert len(log) == 1 and log[0]["delivered"] is True


def test_failing_webhook_does_not_lose_event(env):
    env.api("POST", f"/projects/{env.pid}/alert-rules", json={"kind": "new_issue", "webhook_url": "http://down.test"})

    def boom(*a, **k):
        raise ConnectionError("down")
    env._post = boom
    env.send(make_event())
    env.drain()
    (i,) = issues(env)
    assert i.count == 1
    assert env.api("GET", f"/projects/{env.pid}/alerts").json()[0]["delivered"] is False


def test_email_alert_channel(env, monkeypatch):
    sent = []

    class FakeSMTP:
        def __init__(self, host, port, timeout=None): sent.append(("connect", host, port))
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def starttls(self): sent.append("tls")
        def login(self, u, p): sent.append(("login", u))
        def send_message(self, msg): sent.append(("msg", msg["To"], msg["Subject"]))

    monkeypatch.setenv("SMTP_HOST", "smtp.test")
    monkeypatch.setenv("SMTP_USER", "me")
    r = env.api("POST", f"/projects/{env.pid}/alert-rules", json={"kind": "new_issue", "channel": "email", "email_to": "a@b.co"})
    assert r.status_code == 201 and r.json()["channel"] == "email"
    assert env.api("POST", f"/projects/{env.pid}/alert-rules", json={"kind": "new_issue", "channel": "email"}).status_code == 422
    assert env.api("POST", f"/projects/{env.pid}/alert-rules", json={"kind": "new_issue", "webhook_url": "ftp://x"}).status_code == 422
    env.send(make_event())
    from truti_ingest.worker import run_once
    run_once(env.sync_redis, env.settings, env.Session, "t1", block_ms=1, smtp_factory=FakeSMTP)
    assert ("msg", "a@b.co") == sent[-1][:2] and "new issue" in sent[-1][2]
    assert env.api("GET", f"/projects/{env.pid}/alerts").json()[0]["detail"] == "sent"


def test_email_alert_failure_is_logged_not_raised(env, monkeypatch):
    monkeypatch.delenv("SMTP_HOST", raising=False)
    env.api("POST", f"/projects/{env.pid}/alert-rules", json={"kind": "new_issue", "channel": "email", "email_to": "a@b.co"})
    env.send(make_event())
    env.drain()
    assert issues(env)[0].count == 1
    log = env.api("GET", f"/projects/{env.pid}/alerts").json()[0]
    assert log["delivered"] is False and "SMTP_HOST" in log["detail"]
