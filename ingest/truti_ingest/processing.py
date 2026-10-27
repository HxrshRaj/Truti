"""Turn one validated event into stored rows: symbolicate -> fingerprint -> upsert issue -> insert event."""
import datetime as dt

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from . import alerts
from .db import Event, Issue, Release, SourceMap, utcnow
from .grouping import fingerprint, title_and_culprit
from .symbolicate import SourceMapIndex, symbolicate_frames


def _parse_ts(s):
    d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)


def _symbolicate(session, project_id, event):
    exc = event.get("exception")
    if not exc or event.get("platform") != "javascript" or not event.get("release"):
        return
    cache = {}

    def find_map(filename):
        key = filename.split("?")[0].rsplit("/", 1)[-1]
        if key not in cache:
            row = session.scalar(select(SourceMap).where(
                SourceMap.project_id == project_id, SourceMap.release == event["release"],
                SourceMap.filename == key))
            try:
                cache[key] = SourceMapIndex(row.content) if row else None
            except Exception:
                cache[key] = None
        return cache[key]

    exc["frames"], _ = symbolicate_frames(exc.get("frames", []), find_map)


def process_event(session, project_id, event, post=None):
    """Returns (issue, is_new_issue, stored). ``stored`` is False for a redelivered duplicate."""
    _symbolicate(session, project_id, event)
    fp = fingerprint(event)
    ts = _parse_ts(event["timestamp"])
    release = event.get("release")
    issue = session.scalar(select(Issue).where(Issue.project_id == project_id, Issue.fingerprint == fp))
    is_new = False
    if issue is None:
        title, etype, culprit = title_and_culprit(event)
        issue = Issue(project_id=project_id, fingerprint=fp, title=title, type=etype, culprit=culprit,
                      level=event.get("level", "error"), first_seen=ts, last_seen=ts, count=0,
                      first_release=release, last_release=release)
        try:
            with session.begin_nested():
                session.add(issue)
                session.flush()
            is_new = True
        except IntegrityError:  # another worker created it first
            issue = session.scalar(select(Issue).where(Issue.project_id == project_id, Issue.fingerprint == fp))
    ev = Event(event_id=event["event_id"], project_id=project_id, issue_id=issue.id, timestamp=ts,
               release=release, environment=event.get("environment"), data=event)
    try:
        with session.begin_nested():
            session.add(ev)
            session.flush()
    except IntegrityError:
        session.commit()
        return issue, False, False
    prev = issue.last_seen if issue.last_seen.tzinfo else issue.last_seen.replace(tzinfo=dt.timezone.utc)
    first = issue.first_seen if issue.first_seen.tzinfo else issue.first_seen.replace(tzinfo=dt.timezone.utc)
    values = {"count": Issue.count + 1, "last_seen": max(ts, prev), "first_seen": min(ts, first),
              "level": event.get("level", issue.level),
              "last_environment": event.get("environment")}
    if release:
        values["last_release"] = release
        if issue.first_release is None:
            values["first_release"] = release
        if session.scalar(select(Release.id).where(Release.project_id == project_id, Release.version == release)) is None:
            try:
                with session.begin_nested():
                    session.add(Release(project_id=project_id, version=release, first_seen=utcnow(), event_count=0))
                    session.flush()
            except IntegrityError:
                pass
        session.execute(update(Release).where(Release.project_id == project_id, Release.version == release)
                        .values(event_count=Release.event_count + 1))
    if issue.status == "resolved":
        values["status"] = "unresolved"  # regression
    session.execute(update(Issue).where(Issue.id == issue.id).values(**values))
    session.flush()
    session.refresh(issue)
    alerts.evaluate(session, issue, is_new, post)
    session.commit()
    return issue, is_new, True
