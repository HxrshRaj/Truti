"""Stream consumer. ``python -m truti_ingest.worker``"""
import datetime as dt
import json
import logging
import os
import socket
import time

import redis
from prometheus_client import start_http_server

from sqlalchemy import delete, select

from . import metrics as m
from .alerts import deliver
from .config import Settings
from .db import AlertLog, Event, Issue, make_session_factory, utcnow
from .processing import process_batch, process_event

log = logging.getLogger("truti.worker")
MAX_DELIVERIES = 3


def ensure_group(r, s):
    try:
        r.xgroup_create(s.stream, s.group, id="0", mkstream=True)
    except redis.ResponseError as e:
        if "BUSYGROUP" not in str(e):
            raise


def dead_letter(r, s, msg_id, fields):
    r.xadd(s.dead_stream, fields, maxlen=10_000, approximate=True)
    r.xack(s.stream, s.group, msg_id)
    m.EVENTS_FAILED.inc()


def _parse(fields):
    return int(fields["project_id"]), json.loads(fields["data"]), float(fields.get("received_at", time.time()))


def _store_one(Session, project_id, event, post, smtp_factory):
    with Session() as session:
        try:
            _, _, _, pending = process_event(session, project_id, event)
            session.commit()
        except Exception:
            session.rollback()
            raise
    deliver(Session, pending, post, smtp_factory)


def handle_batch(r, s, Session, batch, post=None, smtp_factory=None):
    """Store a list of (msg_id, fields). One transaction on the fast path; falls back to one event
    at a time if the batch fails so a single bad event cannot block the others. Returns #stored/acked."""
    parsed, acked = [], 0
    for mid, fields in batch:
        try:
            pid, ev, received = _parse(fields)
            m.QUEUE_LAG.observe(max(0.0, time.time() - received))
            parsed.append((mid, pid, ev))
        except Exception:
            log.warning("unparseable message %s; dead-lettering", mid)
            dead_letter(r, s, mid, fields)
    if not parsed:
        return 0
    t0 = time.perf_counter()
    done = []
    try:
        with Session() as session:
            try:
                _, pending = process_batch(session, [(pid, ev) for _, pid, ev in parsed])
                session.commit()
            except Exception:
                session.rollback()
                raise
        deliver(Session, pending, post, smtp_factory)
        done = [mid for mid, _, _ in parsed]
    except Exception:
        log.warning("batch of %d failed; retrying event by event", len(parsed), exc_info=True)
        fields_by_id = dict(batch)
        for mid, pid, ev in parsed:
            try:
                _store_one(Session, pid, ev, post, smtp_factory)
                done.append(mid)
            except Exception:
                log.exception("processing failed for %s", mid)
                pend = r.xpending_range(s.stream, s.group, mid, mid, 1)
                if pend and pend[0].get("times_delivered", 1) >= MAX_DELIVERIES:
                    dead_letter(r, s, mid, fields_by_id[mid])
    if done:
        r.xack(s.stream, s.group, *done)
        per_event = (time.perf_counter() - t0) / len(done)
        for _ in done:
            m.PROCESSING_LATENCY.observe(per_event)  # amortised per event
        m.EVENTS_PROCESSED.inc(len(done))
    return len(done)


def run_once(r, s, Session, consumer, block_ms=1000, count=200, post=None, smtp_factory=None):
    """Read one batch (new messages, then reclaim stale pending ones). Returns messages seen."""
    resp = r.xreadgroup(s.group, consumer, {s.stream: ">"}, count=count, block=block_ms) or []
    batch = [(mid, f) for _, msgs in resp for mid, f in msgs]
    try:  # reclaim messages whose consumer died (idle > 30s)
        claimed = r.xautoclaim(s.stream, s.group, consumer, min_idle_time=30_000, start_id="0-0", count=count)
        batch += [(mid, f) for mid, f in claimed[1] if f]
    except Exception:
        pass
    if batch:
        handle_batch(r, s, Session, batch, post, smtp_factory)
    return len(batch)


def prune(Session, days, now=None):
    """Retention: delete events (and alert history) older than ``days``, then issues left with no
    events whose last_seen is also older. Returns (events_deleted, issues_deleted)."""
    if days <= 0:
        return 0, 0
    cutoff = (now or utcnow()) - dt.timedelta(days=days)
    ev_total = 0
    with Session() as session:
        while True:  # chunked so a big backlog never holds one huge transaction
            ids = session.scalars(select(Event.id).where(Event.timestamp < cutoff).limit(5000)).all()
            if not ids:
                break
            session.execute(delete(Event).where(Event.id.in_(ids)))
            session.commit()
            ev_total += len(ids)
        has_events = select(Event.issue_id).where(Event.issue_id == Issue.id).exists()
        res = session.execute(delete(Issue).where(Issue.last_seen < cutoff, ~has_events))
        session.execute(delete(AlertLog).where(AlertLog.fired_at < cutoff))
        session.commit()
    return ev_total, res.rowcount or 0


def main():
    logging.basicConfig(level=logging.INFO)
    s = Settings()
    r = redis.Redis.from_url(s.redis_url, decode_responses=True)
    Session = make_session_factory(s.database_url, use_migrations=True)
    ensure_group(r, s)
    start_http_server(s.metrics_port, registry=m.REGISTRY)
    consumer = f"{socket.gethostname()}-{os.getpid()}"
    log.info("worker %s started", consumer)
    last_prune = 0.0
    while True:
        try:
            if time.time() - last_prune > 3600:
                last_prune = time.time()
                ev, iss = prune(Session, s.retention_days)
                if ev or iss:
                    log.info("retention: deleted %d events, %d issues", ev, iss)
            m.QUEUE_DEPTH.set(r.xlen(s.stream))
            run_once(r, s, Session, consumer)
        except redis.ConnectionError:
            log.warning("redis unavailable; retrying")
            time.sleep(1)


if __name__ == "__main__":
    main()
