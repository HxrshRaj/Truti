"""Secret scrubbing applied to every event before it leaves the process."""
import re

FILTERED = "[Filtered]"
_KEY_RE = re.compile(
    r"pass(word|wd)?|secret|token|api[-_]?key|authorization|auth|cookie|session|credit|card|cvv|ssn|private[-_]?key",
    re.I,
)
_VALUE_RES = [
    re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]{8,}"),
    re.compile(r"(?i)\b(password|passwd|token|secret|api[-_]?key)=([^&\s]+)"),
]
_MAX_DEPTH = 8


def scrub_string(s):
    s = _VALUE_RES[0].sub("Bearer " + FILTERED, s)
    return _VALUE_RES[1].sub(lambda m: m.group(1) + "=" + FILTERED, s)


def scrub(value, depth=0):
    """Return a copy of ``value`` with sensitive keys and obvious secret strings masked."""
    if depth > _MAX_DEPTH:
        return FILTERED
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            out[k] = FILTERED if isinstance(k, str) and _KEY_RE.search(k) else scrub(v, depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        return [scrub(v, depth + 1) for v in value]
    if isinstance(value, str):
        return scrub_string(value)
    return value
