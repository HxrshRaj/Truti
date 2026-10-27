"""Alert rules: 'new_issue' fires on first sight of a fingerprint; 'spike' fires when an issue
receives >= threshold events within window_seconds (at most once per window per rule).

Delivery channels: 'webhook' (HTTP POST of JSON) or 'email' (SMTP, configured by SMTP_* env vars).
``evaluate`` runs inside the worker's transaction and only *decides* (and stamps ``last_fired``);
``deliver`` runs after commit so a slow webhook or SMTP server never holds the database transaction.
"""
import datetime as dt
import logging
import os
import smtplib
import time
from email.message import EmailMessage

import httpx
from sqlalchemy import func, select

from .db import AlertLog, AlertRule, Event, utcnow

log = logging.getLogger("truti.alerts")
_RULES_TTL = 5.0
_rules_cache: dict = {}


def _aware(d):
    return d if d is None or d.tzinfo else d.replace(tzinfo=dt.timezone.utc)


def _rules(session, project_id):
    """Rules are always read fresh (they carry mutable ``last_fired``); only the "does this project
    have any rules at all" answer is cached, which spares the common no-rules case a query per batch."""
    key = (id(session.get_bind()), project_id)
    hit = _rules_cache.get(key)
    if hit and hit[0] > time.monotonic() and not hit[1]:
        return []
    rows = session.scalars(select(AlertRule).where(AlertRule.project_id == project_id)).all()
    _rules_cache[key] = (time.monotonic() + _RULES_TTL, bool(rows))
    return rows


def clear_cache():
    _rules_cache.clear()


def evaluate(session, issue, is_new):
    """Returns a list of pending deliveries: dicts with rule_id/issue_id/channel/target/payload."""
    out, now = [], utcnow()
    for r in _rules(session, issue.project_id):
        fire = False
        if r.kind == "new_issue" and is_new:
            fire = True
        elif r.kind == "spike":
            since = now - dt.timedelta(seconds=r.window_seconds)
            last = _aware(r.last_fired)
            if last is None or last < since:
                n = session.scalar(select(func.count()).select_from(Event)
                                   .where(Event.issue_id == issue.id, Event.timestamp >= since))
                fire = n >= r.threshold
        if fire:
            r.last_fired = now
            out.append({"rule_id": r.id, "issue_id": issue.id, "channel": r.channel,
                        "target": r.email_to if r.channel == "email" else r.webhook_url,
                        "payload": {"rule_id": r.id, "kind": r.kind, "project_id": issue.project_id,
                                    "issue": {"id": issue.id, "title": issue.title, "count": issue.count,
                                              "level": issue.level, "culprit": issue.culprit}}})
    return out


def _send_email(to, payload, smtp_factory=None):
    host = os.environ.get("SMTP_HOST")
    if not host:
        raise RuntimeError("SMTP_HOST not configured")
    i = payload["issue"]
    msg = EmailMessage()
    msg["Subject"] = f"[Truti] {payload['kind'].replace('_', ' ')}: {i['title'][:120]}"
    msg["From"] = os.environ.get("SMTP_FROM", "truti@localhost")
    msg["To"] = to
    msg.set_content(f"{i['title']}\nlevel: {i['level']}\nculprit: {i['culprit']}\nevents so far: {i['count']}\n")
    with (smtp_factory or smtplib.SMTP)(host, int(os.environ.get("SMTP_PORT", "587")), timeout=5) as s:
        if os.environ.get("SMTP_STARTTLS", "1") == "1":
            s.starttls()
        if os.environ.get("SMTP_USER"):
            s.login(os.environ["SMTP_USER"], os.environ.get("SMTP_PASSWORD", ""))
        s.send_message(msg)


def deliver(Session, pending, post=None, smtp_factory=None):
    """Send each pending alert and record the outcome in alert_log. Never raises."""
    if not pending:
        return
    with Session() as session:
        for p in pending:
            ok, detail = False, ""
            try:
                if p["channel"] == "email":
                    _send_email(p["target"], p["payload"], smtp_factory)
                    ok, detail = True, "sent"
                else:
                    r = (post or httpx.post)(p["target"], json=p["payload"], timeout=3.0)
                    ok, detail = r.status_code < 300, f"HTTP {r.status_code}"
            except Exception as e:  # delivery failure must never break event processing
                detail = repr(e)[:300]
                log.warning("alert delivery failed: %s", detail)
            session.add(AlertLog(rule_id=p["rule_id"], issue_id=p["issue_id"], delivered=ok, detail=detail))
        session.commit()
