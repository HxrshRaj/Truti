"""Stream consumer. ``python -m truti_ingest.worker``"""
import json
import logging
import os
import socket
import time

import redis
from prometheus_client import start_http_server

from . import metrics as m
from .config import Settings
from .db import make_session_factory
from .processing import process_event

log = logging.getLogger("truti.worker")
MAX_DELIVERIES = 3


def ensure_group(r, s):
    try:
        r.xgroup_create(s.stream, s.group, id="0", mkstream=True)
    except redis.ResponseError as e:
        if "BUSYGROUP" not in str(e):
            raise


def handle(r, s, Session, msg_id, fields, post=None):
    try:
        project_id = int(fields["project_id"])
        event = json.loads(fields["data"])
        m.QUEUE_LAG.observe(max(0.0, time.time() - float(fields.get("received_at", time.time()))))
        t0 = time.perf_counter()
        with Session() as session:
            process_event(session, project_id, event, post)
        m.PROCESSING_LATENCY.observe(time.perf_counter() - t0)
        m.EVENTS_PROCESSED.inc()
        r.xack(s.stream, s.group, msg_id)
        return True
    except Exception:
        log.exception("processing failed for %s", msg_id)
        return False


def dead_letter(r, s, msg_id, fields):
    r.xadd(s.dead_stream, fields, maxlen=10_000, approximate=True)
    r.xack(s.stream, s.group, msg_id)
    m.EVENTS_FAILED.inc()


def run_once(r, s, Session, consumer, block_ms=1000, count=100, post=None):
    """Read one batch (new messages, then reclaim stale pending ones). Returns messages handled."""
    n = 0
    resp = r.xreadgroup(s.group, consumer, {s.stream: ">"}, count=count, block=block_ms) or []
    batch = [(mid, f) for _, msgs in resp for mid, f in msgs]
    # reclaim messages whose consumer died (idle > 30s)
    try:
        claimed = r.xautoclaim(s.stream, s.group, consumer, min_idle_time=30_000, start_id="0-0", count=count)
        batch += [(mid, f) for mid, f in claimed[1] if f]
    except Exception:
        pass
    for mid, fields in batch:
        n += 1
        if handle(r, s, Session, mid, fields, post):
            continue
        pend = r.xpending_range(s.stream, s.group, mid, mid, 1)
        if pend and pend[0].get("times_delivered", 1) >= MAX_DELIVERIES:
            dead_letter(r, s, mid, fields)
    return n


def main():
    logging.basicConfig(level=logging.INFO)
    s = Settings()
    r = redis.Redis.from_url(s.redis_url, decode_responses=True)
    Session = make_session_factory(s.database_url)
    ensure_group(r, s)
    start_http_server(s.metrics_port, registry=m.REGISTRY)
    consumer = f"{socket.gethostname()}-{os.getpid()}"
    log.info("worker %s started", consumer)
    while True:
        try:
            m.QUEUE_DEPTH.set(r.xlen(s.stream))
            run_once(r, s, Session, consumer)
        except redis.ConnectionError:
            log.warning("redis unavailable; retrying")
            time.sleep(1)


if __name__ == "__main__":
    main()
