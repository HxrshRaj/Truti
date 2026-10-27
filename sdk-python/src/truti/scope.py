"""Per-context state (tags, user, breadcrumbs). Uses contextvars so asyncio tasks
and request handlers can isolate state with ``push_scope``."""
import contextvars
import copy
import datetime as _dt
from collections import deque

MAX_BREADCRUMBS = 100


def now_iso():
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class Scope:
    def __init__(self):
        self.tags = {}
        self.user = None
        self.breadcrumbs = deque(maxlen=MAX_BREADCRUMBS)
        self.request = None

    def copy(self):
        s = Scope()
        s.tags = dict(self.tags)
        s.user = copy.copy(self.user)
        s.breadcrumbs = deque(self.breadcrumbs, maxlen=MAX_BREADCRUMBS)
        s.request = self.request
        return s


_global_scope = Scope()
_scope_var = contextvars.ContextVar("truti_scope", default=None)


def get_scope():
    return _scope_var.get() or _global_scope


class push_scope:
    """Context manager giving the enclosed code (and tasks it spawns) its own copy of the scope."""

    def __enter__(self):
        self._scope = get_scope().copy()
        self._token = _scope_var.set(self._scope)
        return self._scope

    def __exit__(self, *exc):
        _scope_var.reset(self._token)
        return False


def reset_global_scope():
    global _global_scope
    _global_scope = Scope()
    _scope_var.set(None)
