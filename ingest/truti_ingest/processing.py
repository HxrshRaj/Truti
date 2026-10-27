"""Turn validated events into stored rows: symbolicate -> fingerprint -> upsert issue -> insert event.

Two paths, same result:

* ``process_batch`` (fast path): one transaction for a whole batch of stream messages. Duplicate ids
  are found with one query, events are bulk-inserted, and each touched issue/release gets a single
  UPDATE with the summed deltas. This is what removes the per-event commit that capped the first
  worker at ~90 events/s.
* ``process_event`` (slow path): one event at a time with savepoints. Used to retry a batch that
  failed (e.g. two workers created the same new issue at once), so one bad event cannot sink a batch.

Neither commits; the caller commits and then delivers the returned alerts.
"""
import datetime as dt

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from . import alerts
from .db import Event, FingerprintAlias, Issue, Release, SourceMap, utcnow
from .grouping import fingerprint, title_and_culprit
from .symbolicate import SourceMapIndex, symbolicate_frames


def _aware(d):
    return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)


def _parse_ts(s):
    return _aware(dt.datetime.fromisoformat(s.replace("Z", "+00:00")))


def _symbolicate(session, project_id, event, map_cache):
    exc = event.get("exception")
    if not exc or event.get("platform") != "javascript" or not event.get("release"):
        return

    def find_map(filename):
        key = (project_id, event["release"], filename.split("?")[0].rsplit("/", 1)[-1])
        if key not in map_cache:
            row = session.scalar(select(SourceMap).where(
                SourceMap.project_id == key[0], SourceMap.release == key[1], SourceMap.filename == key[2]))
            try:
                map_cache[key] = SourceMapIndex(row.content) if row else None
            except Exception:
                map_cache[key] = None
        return map_cache[key]

    exc["frames"], _ = symbolicate_frames(exc.get("frames", []), find_map)


def _prepare(session, project_id, event, map_cache):
    _symbolicate(session, project_id, event, map_cache)
    return fingerprint(event), _parse_ts(event["timestamp"])


def _find_issue(session, project_id, fp):
    issue = session.scalar(select(Issue).where(Issue.project_id == project_id, Issue.fingerprint == fp))
    if issue is None:  # maybe merged into another issue
        issue = session.scalar(select(Issue).join(FingerprintAlias, FingerprintAlias.issue_id == Issue.id).where(
            FingerprintAlias.project_id == project_id, FingerprintAlias.fingerprint == fp))
    return issue


def _new_issue(project_id, fp, event, ts):
    title, etype, culprit = title_and_culprit(event)
    release = event.get("release")
    return Issue(project_id=project_id, fingerprint=fp, title=title, type=etype, culprit=culprit,
                 level=event.get("level", "error"), first_seen=ts, last_seen=ts, count=0,
                 first_release=release, last_release=release)


def _ensure_release(session, project_id, version, known):
    if (project_id, version) in known:
        return
    if session.scalar(select(Release.id).where(Release.project_id == project_id, Release.version == version)) is None:
        session.add(Release(project_id=project_id, version=version, first_seen=utcnow(), event_count=0))
        session.flush()
    known.add((project_id, version))


_KNOWN_RELEASES: dict = {}  # per database engine: releases already known to exist


def process_batch(session, items):
    """items: list of (project_id, event dict). Returns (stored_count, pending_alerts)."""
    if not items:
        return 0, []
    map_cache, issues, touched, rel_counts, new_ids = {}, {}, {}, {}, set()
    by_project = {}
    for pid, ev in items:
        by_project.setdefault(pid, set()).add(ev["event_id"])
    existing = set()
    for pid, ids in by_project.items():
        existing |= {(pid, e) for e in session.scalars(
            select(Event.event_id).where(Event.project_id == pid, Event.event_id.in_(ids)))}
    rows, seen_ids = [], set()
    for pid, ev in items:
        key = (pid, ev["event_id"])
        if key in existing or key in seen_ids:
            continue
        seen_ids.add(key)
        fp, ts = _prepare(session, pid, ev, map_cache)
        ik = (pid, fp)
        issue = issues.get(ik)
        if issue is None:
            issue = _find_issue(session, pid, fp)
            if issue is None:
                issue = _new_issue(pid, fp, ev, ts)
                session.add(issue)
                session.flush()  # need the id; rare (only first sight of a fingerprint)
                new_ids.add(issue.id)
            issues[ik] = issue
        t = touched.setdefault(issue.id, {"issue": issue, "n": 0, "first": ts, "last": ts, "ev": ev,
                                         "release": None, "reopen": issue.status == "resolved"})
        t["n"] += 1
        t["first"], t["last"] = min(t["first"], ts), max(t["last"], ts)
        if ts >= t["last"]:
            t["ev"] = ev
        rel = ev.get("release")
        if rel:
            t["release"] = rel
            rel_counts[(pid, rel)] = rel_counts.get((pid, rel), 0) + 1
        rows.append(Event(event_id=ev["event_id"], project_id=pid, issue_id=issue.id, timestamp=ts,
                          release=rel, environment=ev.get("environment"), data=ev))
    session.add_all(rows)
    for (pid, rel), n in rel_counts.items():
        _ensure_release(session, pid, rel, _KNOWN_RELEASES.setdefault(id(session.get_bind()), set()))
        session.execute(update(Release).where(Release.project_id == pid, Release.version == rel)
                        .values(event_count=Release.event_count + n))
    pending = []
    session.flush()
    for iid, t in touched.items():
        issue = t["issue"]
        values = {"count": Issue.count + t["n"], "last_seen": max(t["last"], _aware(issue.last_seen)),
                  "first_seen": min(t["first"], _aware(issue.first_seen)),
                  "level": t["ev"].get("level", issue.level), "last_environment": t["ev"].get("environment")}
        if t["release"]:
            values["last_release"] = t["release"]
            if issue.first_release is None:
                values["first_release"] = t["release"]
        if t["reopen"]:
            values["status"] = "unresolved"  # regression
        session.execute(update(Issue).where(Issue.id == iid).values(**values))
    session.flush()
    for iid, t in touched.items():
        session.refresh(t["issue"])
        pending += alerts.evaluate(session, t["issue"], iid in new_ids)
    return len(rows), pending


def process_event(session, project_id, event):
    """Slow path. Returns (issue, is_new_issue, stored, pending_alerts). Never commits."""
    map_cache = {}
    fp, ts = _prepare(session, project_id, event, map_cache)
    issue = _find_issue(session, project_id, fp)
    is_new = False
    if issue is None:
        issue = _new_issue(project_id, fp, event, ts)
        try:
            with session.begin_nested():
                session.add(issue)
                session.flush()
            is_new = True
        except IntegrityError:  # another worker created it first
            issue = session.scalar(select(Issue).where(Issue.project_id == project_id, Issue.fingerprint == fp))
    ev = Event(event_id=event["event_id"], project_id=project_id, issue_id=issue.id, timestamp=ts,
               release=event.get("release"), environment=event.get("environment"), data=event)
    try:
        with session.begin_nested():
            session.add(ev)
            session.flush()
    except IntegrityError:
        return issue, False, False, []
    release = event.get("release")
    values = {"count": Issue.count + 1, "last_seen": max(ts, _aware(issue.last_seen)),
              "first_seen": min(ts, _aware(issue.first_seen)), "level": event.get("level", issue.level),
              "last_environment": event.get("environment")}
    if release:
        values["last_release"] = release
        if issue.first_release is None:
            values["first_release"] = release
        try:
            with session.begin_nested():
                _ensure_release(session, project_id, release, set())
        except IntegrityError:
            pass
        session.execute(update(Release).where(Release.project_id == project_id, Release.version == release)
                        .values(event_count=Release.event_count + 1))
    if issue.status == "resolved":
        values["status"] = "unresolved"
    session.execute(update(Issue).where(Issue.id == issue.id).values(**values))
    session.flush()
    session.refresh(issue)
    return issue, is_new, True, alerts.evaluate(session, issue, is_new)
