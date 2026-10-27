"""Alert rules: 'new_issue' fires on first sight of a fingerprint; 'spike' fires when an issue
receives >= threshold events within window_seconds (at most once per window per rule)."""
import datetime as dt
import logging

import httpx
from sqlalchemy import func, select

from .db import AlertLog, AlertRule, Event, utcnow

log = logging.getLogger("truti.alerts")


def _aware(d):
    return d if d is None or d.tzinfo else d.replace(tzinfo=dt.timezone.utc)


def evaluate(session, issue, is_new, post=None):
    rules = session.scalars(select(AlertRule).where(AlertRule.project_id == issue.project_id)).all()
    now = utcnow()
    for r in rules:
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
            _fire(session, r, issue, now, post)


def _fire(session, rule, issue, now, post):
    payload = {"rule_id": rule.id, "kind": rule.kind, "project_id": issue.project_id,
               "issue": {"id": issue.id, "title": issue.title, "count": issue.count,
                         "level": issue.level, "culprit": issue.culprit}}
    ok, detail = False, ""
    try:
        r = (post or httpx.post)(rule.webhook_url, json=payload, timeout=3.0)
        ok, detail = r.status_code < 300, f"HTTP {r.status_code}"
    except Exception as e:  # webhook failure must never break event processing
        detail = repr(e)[:300]
        log.warning("alert webhook failed: %s", detail)
    rule.last_fired = now
    session.add(AlertLog(rule_id=rule.id, issue_id=issue.id, fired_at=now, delivered=ok, detail=detail))
