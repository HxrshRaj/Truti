"""Builds versioned event/envelope dicts (see docs/envelope.md)."""
import linecache
import os
import sysconfig
import traceback
import uuid

from . import __version__
from .scope import now_iso

ENVELOPE_VERSION = 1
CONTEXT_LINES = 5
MAX_FRAMES = 100
_STDLIB = os.path.normcase(sysconfig.get_paths().get("stdlib", "\0"))


def _is_in_app(filename):
    n = os.path.normcase(filename)
    return "site-packages" not in n and "dist-packages" not in n and not n.startswith(_STDLIB)


def frames_from_traceback(tb):
    frames = []
    for frame, lineno in traceback.walk_tb(tb):
        filename = frame.f_code.co_filename
        lines = linecache.getlines(filename, frame.f_globals)
        ctx, pre, post = None, [], []
        if lines and 0 < lineno <= len(lines):
            ctx = lines[lineno - 1].rstrip("\n")
            pre = [x.rstrip("\n") for x in lines[max(0, lineno - 1 - CONTEXT_LINES): lineno - 1]]
            post = [x.rstrip("\n") for x in lines[lineno: lineno + CONTEXT_LINES]]
        frames.append({
            "filename": filename,
            "function": frame.f_code.co_name,
            "lineno": lineno,
            "context_line": ctx,
            "pre_context": pre,
            "post_context": post,
            "in_app": _is_in_app(filename),
        })
    return frames[-MAX_FRAMES:]


def build_event(*, exc_info=None, message=None, level="error", release=None, environment=None, scope=None):
    event = {
        "event_id": uuid.uuid4().hex,
        "timestamp": now_iso(),
        "platform": "python",
        "level": level,
        "release": release,
        "environment": environment,
        "sdk": {"name": "truti.python", "version": __version__},
    }
    if message is not None:
        event["message"] = str(message)
    if exc_info and exc_info[0] is not None:
        etype, value, tb = exc_info
        event["exception"] = {
            "type": etype.__name__,
            "value": str(value),
            "frames": frames_from_traceback(tb),
        }
        event.setdefault("message", f"{etype.__name__}: {value}")
    if scope is not None:
        event["tags"] = dict(scope.tags)
        event["user"] = scope.user
        event["breadcrumbs"] = list(scope.breadcrumbs)
        if scope.request:
            event["request"] = scope.request
    return event


def build_envelope(events):
    return {"envelope_version": ENVELOPE_VERSION, "sent_at": now_iso(), "events": events}
