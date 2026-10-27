"""Background, batching, retrying transport. Nothing in here may raise into the host app."""
import json
import logging
import queue
import random
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from . import __version__
from .envelope import build_envelope

log = logging.getLogger("truti")
log.addHandler(logging.NullHandler())


def parse_dsn(dsn):
    """``https://PUBLICKEY@host[:port][/prefix]/PROJECT_ID`` -> (envelope_url, public_key)."""
    p = urlsplit(dsn)
    if not (p.scheme and p.hostname and p.username):
        raise ValueError("invalid DSN: expected scheme://publickey@host/project_id")
    parts = [x for x in p.path.split("/") if x]
    if not parts:
        raise ValueError("invalid DSN: missing project id")
    project_id, prefix = parts[-1], "/".join(parts[:-1])
    host = p.hostname + (f":{p.port}" if p.port else "")
    base = f"{p.scheme}://{host}" + (f"/{prefix}" if prefix else "")
    return f"{base}/api/{project_id}/envelope/", p.username


class Transport:
    def __init__(self, dsn, *, max_queue=100, batch_size=20, flush_interval=0.5, max_retries=5,
                 base_delay=0.5, max_delay=30.0, timeout=5.0):
        self.url, self.key = parse_dsn(dsn)
        self.batch_size = batch_size
        self.flush_interval = flush_interval
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.timeout = timeout
        self._q = queue.Queue(maxsize=max_queue)
        self._stop = threading.Event()
        self._cond = threading.Condition()
        self._pending = 0
        self._thread = None
        self._thread_lock = threading.Lock()
        self.stats = {"sent": 0, "dropped_queue_full": 0, "dropped_failed": 0, "retries": 0}

    # -- public -----------------------------------------------------------
    def enqueue(self, event):
        try:
            self._ensure_thread()
            with self._cond:
                self._pending += 1
            try:
                self._q.put_nowait(event)
                return True
            except queue.Full:
                self._done(1)
                self.stats["dropped_queue_full"] += 1
                return False
        except Exception:  # pragma: no cover - defensive
            return False

    def flush(self, timeout=2.0):
        """Block until everything queued has been delivered or dropped. True if drained."""
        deadline = time.monotonic() + timeout
        with self._cond:
            while self._pending > 0:
                left = deadline - time.monotonic()
                if left <= 0:
                    return False
                self._cond.wait(left)
        return True

    def close(self, timeout=2.0):
        self.flush(timeout)
        self._stop.set()

    # -- internals --------------------------------------------------------
    def _ensure_thread(self):
        if self._thread is not None and self._thread.is_alive():
            return
        with self._thread_lock:
            if self._thread is None or not self._thread.is_alive():
                self._stop.clear()
                self._thread = threading.Thread(target=self._run, name="truti-transport", daemon=True)
                self._thread.start()

    def _done(self, n):
        with self._cond:
            self._pending -= n
            self._cond.notify_all()

    def _run(self):
        while not self._stop.is_set():
            try:
                batch = [self._q.get(timeout=self.flush_interval)]
            except queue.Empty:
                continue
            while len(batch) < self.batch_size:
                try:
                    batch.append(self._q.get_nowait())
                except queue.Empty:
                    break
            try:
                ok = self._deliver(batch)
            except Exception:  # pragma: no cover - defensive
                ok = False
            self.stats["sent" if ok else "dropped_failed"] += len(batch)
            self._done(len(batch))

    def _deliver(self, batch):
        body = json.dumps(build_envelope(batch), default=str).encode()
        for attempt in range(self.max_retries + 1):
            status, retry_after = self._post(body)
            if status is not None and 200 <= status < 300:
                return True
            if status is not None and status != 429 and status < 500:
                log.debug("truti: server rejected batch with %s; dropping", status)
                return False
            if attempt == self.max_retries:
                break
            self.stats["retries"] += 1
            if retry_after is not None:
                delay = min(retry_after, self.max_delay)
            else:
                delay = min(self.max_delay, self.base_delay * (2 ** attempt))
                delay = random.uniform(0, delay)  # full jitter
            if self._stop.wait(delay):
                return False
        return False

    def _post(self, body):
        req = urllib.request.Request(self.url, data=body, method="POST", headers={
            "Content-Type": "application/json",
            "X-Truti-Auth": f"truti_key={self.key}",
            "User-Agent": f"truti.python/{__version__}",
        })
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return r.status, None
        except urllib.error.HTTPError as e:
            ra = e.headers.get("Retry-After") if e.headers else None
            try:
                ra = float(ra) if ra is not None else None
            except ValueError:
                ra = None
            return e.code, ra
        except Exception as e:  # connection refused, timeout, DNS ...
            log.debug("truti: send failed: %r", e)
            return None, None
