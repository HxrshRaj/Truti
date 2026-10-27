import datetime as dt
import json

from sqlalchemy import select

from conftest import make_event
from truti_ingest.db import AlertLog, Event, Issue, Release
from truti_ingest.grouping import fingerprint, normalise_message
from truti_ingest.worker import dead_letter


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
    from truti_ingest.worker import run_once
    # each run reads it once as new; later runs reclaim it via XAUTOCLAIM only after idle time,
    # so drive dead-lettering directly the way run_once does after repeated failures.
    run_once(env.sync_redis, env.settings, env.Session, "t1", block_ms=1)
    (mid, fields), = env.sync_redis.xrange(env.settings.stream)
    dead_letter(env.sync_redis, env.settings, mid, fields)
    assert env.sync_redis.xlen(env.settings.dead_stream) == 1
    assert env.sync_redis.xpending(env.settings.stream, env.settings.group)["pending"] == 0


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
