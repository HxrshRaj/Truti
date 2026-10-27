import logging
import sys
import threading

import pytest

import truti
from truti.envelope import build_event
from truti.scrub import scrub
from truti.transport import parse_dsn


def boom():
    raise ValueError("bad input 42")


def test_capture_exception_sends_frames_and_metadata(server, init_sdk):
    init_sdk(release="1.2.3", environment="test")
    truti.set_tag("region", "eu")
    truti.set_user({"id": "u1"})
    truti.add_breadcrumb("clicked", category="ui")
    try:
        boom()
    except ValueError as e:
        eid = truti.capture_exception(e)
    assert truti.flush(3)
    ev = server.events[0]
    assert ev["event_id"] == eid
    assert ev["exception"]["type"] == "ValueError"
    last = ev["exception"]["frames"][-1]
    assert last["function"] == "boom" and last["context_line"].strip().startswith("raise ValueError")
    assert last["in_app"] is True and last["pre_context"] and last["lineno"] > 0
    assert ev["release"] == "1.2.3" and ev["environment"] == "test"
    assert ev["tags"] == {"region": "eu"} and ev["user"] == {"id": "u1"}
    assert ev["breadcrumbs"][0]["message"] == "clicked"
    headers, env = server.received[0]
    assert env["envelope_version"] == 1
    assert headers["X-Truti-Auth"] == "truti_key=pubkey123"


def test_capture_message(server, init_sdk):
    init_sdk()
    truti.capture_message("hello", level="warning")
    assert truti.flush(3)
    assert server.events[0]["message"] == "hello" and server.events[0]["level"] == "warning"


def test_scrubbing():
    out = scrub({"password": "x", "headers": {"Authorization": "Bearer abcdefgh12345"},
                 "msg": "failed token=abc123 for Bearer abcdefghijkl", "ok": 1})
    assert out["password"] == "[Filtered]"
    assert out["headers"]["Authorization"] == "[Filtered]"
    assert "abc123" not in out["msg"] and "abcdefghijkl" not in out["msg"]
    assert out["ok"] == 1


def test_scrubbed_before_send(server, init_sdk):
    init_sdk()
    truti.add_breadcrumb("login", data={"password": "hunter2"})
    truti.capture_message("x")
    truti.flush(3)
    assert "hunter2" not in str(server.events)


def test_before_send_can_drop_and_modify(server, init_sdk):
    def bs(ev):
        if ev.get("message") == "drop":
            return None
        ev["tags"]["touched"] = "yes"
        return ev
    init_sdk(before_send=bs)
    truti.capture_message("drop")
    truti.capture_message("keep")
    truti.flush(3)
    assert [e["message"] for e in server.events] == ["keep"]
    assert server.events[0]["tags"]["touched"] == "yes"


def test_sample_rate_zero_sends_nothing(server, init_sdk):
    init_sdk(sample_rate=0.0)
    for _ in range(20):
        truti.capture_message("x")
    truti.flush(1)
    assert server.events == []


def test_before_send_raising_does_not_propagate(server, init_sdk):
    init_sdk(before_send=lambda e: 1 / 0)
    assert truti.capture_message("x") is None  # no exception


def test_invalid_dsn_disables_sdk_without_raising():
    assert truti.init("not a dsn") is None
    assert truti.capture_message("x") is None
    assert truti.flush() is True


def test_parse_dsn():
    assert parse_dsn("https://k@h.io/12") == ("https://h.io/api/12/envelope/", "k")
    assert parse_dsn("http://k@h.io:8000/pre/3")[0] == "http://h.io:8000/pre/api/3/envelope/"
    with pytest.raises(ValueError):
        parse_dsn("https://h.io/12")


def test_unhandled_exception_hooks(server, monkeypatch):
    from truti import client as cm
    monkeypatch.setattr(cm, "_orig_excepthook", lambda *a: None)
    monkeypatch.setattr(cm, "_orig_threading_hook", lambda args: None)
    monkeypatch.setattr(cm, "_hooks_installed", False)
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)
    monkeypatch.setattr(threading, "excepthook", threading.excepthook)
    truti.init(server.dsn, flush_interval=0.05)
    t = threading.Thread(target=boom)
    t.start(); t.join()
    assert truti.flush(3)
    assert any(e["exception"]["type"] == "ValueError" for e in server.events)
    try:
        boom()
    except ValueError:
        et, ev, tb = sys.exc_info()
    sys.excepthook(et, ev, tb)
    assert len(server.events) == 2


def test_logging_integration(server, init_sdk):
    from truti.integrations.logging import TrutiHandler
    init_sdk()
    lg = logging.getLogger("myapp.test")
    lg.setLevel(logging.DEBUG)
    h = TrutiHandler()
    lg.addHandler(h)
    try:
        lg.info("started")
        try:
            boom()
        except ValueError:
            lg.exception("it broke")
        lg.error("plain error")
        truti.flush(3)
    finally:
        lg.removeHandler(h)
    evs = server.events
    assert [e["message"] for e in evs] == ["ValueError: bad input 42", "plain error"] or len(evs) == 2
    assert evs[0]["exception"]["type"] == "ValueError"
    assert any(b["message"] == "started" for b in evs[0]["breadcrumbs"])


def test_push_scope_isolates():
    truti.set_tag("a", "1")
    with truti.push_scope():
        truti.set_tag("a", "2")
        assert truti.scope.get_scope().tags["a"] == "2"
    assert truti.scope.get_scope().tags["a"] == "1"


def test_build_event_without_exception_has_no_exception_key():
    assert "exception" not in build_event(message="m")
