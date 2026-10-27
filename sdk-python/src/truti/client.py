import atexit
import logging
import random
import sys
import threading

from .envelope import build_event
from .scope import get_scope, now_iso
from .scrub import scrub
from .transport import Transport

log = logging.getLogger("truti")


class Client:
    def __init__(self, dsn, release=None, environment=None, sample_rate=1.0, before_send=None,
                 transport=None, **transport_opts):
        self.release = release
        self.environment = environment
        self.sample_rate = sample_rate
        self.before_send = before_send
        self.transport = transport or Transport(dsn, **transport_opts)

    def capture_event(self, event):
        """Sample, scrub, run before_send, enqueue. Returns event_id or None. Never raises."""
        try:
            if self.sample_rate < 1.0 and random.random() >= self.sample_rate:
                return None
            event = scrub(event)
            if self.before_send is not None:
                event = self.before_send(event)
                if event is None:
                    return None
            return event["event_id"] if self.transport.enqueue(event) else None
        except Exception:
            log.debug("truti: capture failed", exc_info=True)
            return None

    def capture_exception(self, exc=None, level="error"):
        try:
            if exc is None:
                exc_info = sys.exc_info()
            elif isinstance(exc, tuple):
                exc_info = exc
            else:
                exc_info = (type(exc), exc, exc.__traceback__)
            if exc_info[0] is None:
                return None
            return self.capture_event(build_event(
                exc_info=exc_info, level=level, release=self.release,
                environment=self.environment, scope=get_scope()))
        except Exception:
            log.debug("truti: capture_exception failed", exc_info=True)
            return None

    def capture_message(self, message, level="info"):
        try:
            return self.capture_event(build_event(
                message=message, level=level, release=self.release,
                environment=self.environment, scope=get_scope()))
        except Exception:
            return None

    def flush(self, timeout=2.0):
        try:
            return self.transport.flush(timeout)
        except Exception:
            return False


_client = None
_hooks_installed = False
_orig_excepthook = sys.excepthook
_orig_threading_hook = getattr(threading, "excepthook", None)


def get_client():
    return _client


def _install_hooks():
    global _hooks_installed
    if _hooks_installed:
        return
    _hooks_installed = True

    def excepthook(etype, value, tb):
        c = _client
        if c is not None and not issubclass(etype, KeyboardInterrupt):
            c.capture_exception((etype, value, tb), level="fatal")
            c.flush(2.0)
        _orig_excepthook(etype, value, tb)

    sys.excepthook = excepthook

    if _orig_threading_hook is not None:
        def thread_hook(args):
            c = _client
            if c is not None and args.exc_type is not SystemExit:
                c.capture_exception((args.exc_type, args.exc_value, args.exc_traceback), level="fatal")
            _orig_threading_hook(args)

        threading.excepthook = thread_hook

    atexit.register(lambda: _client and _client.flush(2.0))


def init(dsn=None, release=None, environment=None, sample_rate=1.0, before_send=None,
         auto_hooks=True, **transport_opts):
    """Initialise the SDK. An invalid or missing DSN disables the SDK instead of raising."""
    global _client
    try:
        if not dsn:
            _client = None
            return None
        _client = Client(dsn, release, environment, sample_rate, before_send, **transport_opts)
        if auto_hooks:
            _install_hooks()
        return _client
    except Exception:
        log.warning("truti: init failed; SDK disabled", exc_info=True)
        _client = None
        return None


def capture_exception(exc=None, level="error"):
    return _client.capture_exception(exc, level) if _client else None


def capture_message(message, level="info"):
    return _client.capture_message(message, level) if _client else None


def flush(timeout=2.0):
    return _client.flush(timeout) if _client else True


def close(timeout=2.0):
    """Flush, stop the background worker and disable the SDK."""
    global _client
    c, _client = _client, None
    if c is not None:
        try:
            c.transport.close(timeout)
        except Exception:
            pass


def set_user(user):
    get_scope().user = dict(user) if user else None


def set_tag(key, value):
    get_scope().tags[str(key)] = str(value)


def add_breadcrumb(message, category="default", level="info", data=None):
    try:
        crumb = {"timestamp": now_iso(), "category": category, "message": str(message), "level": level}
        if data:
            crumb["data"] = data
        get_scope().breadcrumbs.append(crumb)
    except Exception:
        pass
