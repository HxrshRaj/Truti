"""Fingerprinting: which events belong to the same issue.

Exception events: sha1 over the exception type plus the *stack signature* — for the last
STACK_DEPTH in-app frames (all frames if none are in-app): (basename-normalised filename,
function, stripped context_line). Line numbers and the exception *value* are deliberately
excluded so that edits elsewhere in a file or different ids in the message do not split issues.

Message events: sha1 over the message with digits/hex/uuids/quoted strings collapsed.
"""
import hashlib
import re

STACK_DEPTH = 5
_NUM = re.compile(r"\b(0x[0-9a-fA-F]+|[0-9a-fA-F]{8}-[0-9a-fA-F-]{27}|\d+)\b")
_QUOTED = re.compile(r"(['\"]).*?\1")


def normalise_message(msg: str) -> str:
    msg = _QUOTED.sub("<str>", msg)
    return _NUM.sub("<n>", msg).strip()


def _frame_sig(f: dict):
    return (f.get("filename", "").replace("\\", "/").split("?")[0],
            f.get("function") or "?",
            (f.get("context_line") or "").strip())


def fingerprint(event: dict) -> str:
    exc = event.get("exception")
    if exc:
        frames = exc.get("frames") or []
        app = [f for f in frames if f.get("in_app") is not False]
        used = (app or frames)[-STACK_DEPTH:]
        parts = [exc["type"]] + ["|".join(map(str, _frame_sig(f))) for f in used]
    else:
        parts = ["message", normalise_message(event.get("message") or "")]
    return hashlib.sha1("\n".join(parts).encode()).hexdigest()


def title_and_culprit(event: dict):
    exc = event.get("exception")
    if exc:
        value = (exc.get("value") or "").split("\n")[0][:300]
        title = f"{exc['type']}: {value}" if value else exc["type"]
        frames = exc.get("frames") or []
        app = [f for f in frames if f.get("in_app") is not False] or frames
        culprit = None
        if app:
            f = app[-1]
            culprit = f"{f.get('function') or '?'} ({f.get('filename')}:{f.get('lineno')})"
        return title, exc["type"], culprit
    return (event.get("message") or "")[:300], None, None
