"""Dashboard API, mounted at /api/0. Auth: ``Authorization: Bearer <ADMIN_TOKEN>``."""
import datetime as dt
import hmac

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, or_, select

from .db import AlertLog, AlertRule, Event, Issue, Project, Release, SourceMap, utcnow
from .symbolicate import SourceMapIndex

SPARK_BUCKETS = 24


def _iso(d):
    if d is None:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return d.isoformat().replace("+00:00", "Z")


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class IssuePatch(BaseModel):
    status: str = Field(pattern="^(unresolved|resolved|ignored)$")


class RuleIn(BaseModel):
    kind: str = Field(pattern="^(new_issue|spike)$")
    webhook_url: str = Field(min_length=8, max_length=2000)
    threshold: int = Field(default=10, ge=1, le=100000)
    window_seconds: int = Field(default=60, ge=1, le=86400)


def make_router(s, Session):
    router = APIRouter()

    def db():
        with Session() as session:
            yield session

    def admin(request: Request):
        tok = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        if not hmac.compare_digest(tok, s.admin_token):
            raise HTTPException(401, "invalid admin token")

    dep = [Depends(admin)]

    def project_or_404(session, pid):
        p = session.get(Project, pid)
        if not p:
            raise HTTPException(404, "project not found")
        return p

    def project_json(p, request):
        host = request.headers.get("host", request.url.netloc)
        scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
        return {"id": p.id, "name": p.name, "public_key": p.public_key,
                "dsn": f"{scheme}://{p.public_key}@{host}/{p.id}", "created_at": _iso(p.created_at)}

    @router.get("/projects", dependencies=dep)
    def list_projects(request: Request, session=Depends(db)):
        return [project_json(p, request) for p in session.scalars(select(Project).order_by(Project.id))]

    @router.post("/projects", dependencies=dep, status_code=201)
    def create_project(body: ProjectIn, request: Request, session=Depends(db)):
        p = Project(name=body.name)
        session.add(p)
        session.commit()
        return project_json(p, request)

    def sparklines(session, issue_ids):
        now = utcnow()
        start = now.replace(minute=0, second=0, microsecond=0) - dt.timedelta(hours=SPARK_BUCKETS - 1)
        out = {i: [0] * SPARK_BUCKETS for i in issue_ids}
        if not issue_ids:
            return out
        rows = session.execute(select(Event.issue_id, Event.timestamp)
                               .where(Event.issue_id.in_(issue_ids), Event.timestamp >= start))
        for iid, ts in rows:
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=dt.timezone.utc)
            b = int((ts - start).total_seconds() // 3600)
            if 0 <= b < SPARK_BUCKETS:
                out[iid][b] += 1
        return out

    def issue_json(i, spark=None):
        d = {"id": i.id, "project_id": i.project_id, "title": i.title, "type": i.type,
             "culprit": i.culprit, "level": i.level, "status": i.status, "count": i.count,
             "first_seen": _iso(i.first_seen), "last_seen": _iso(i.last_seen),
             "first_release": i.first_release, "last_release": i.last_release,
             "environment": i.last_environment}
        if spark is not None:
            d["sparkline"] = spark
        return d

    @router.get("/projects/{pid}/issues", dependencies=dep)
    def list_issues(pid: int, query: str = "", status: str = "", environment: str = "", release: str = "",
                    level: str = "", sort: str = "last_seen", limit: int = 50, offset: int = 0,
                    session=Depends(db)):
        project_or_404(session, pid)
        q = select(Issue).where(Issue.project_id == pid)
        if query:
            like = f"%{query}%"
            q = q.where(or_(Issue.title.ilike(like), Issue.culprit.ilike(like), Issue.type.ilike(like)))
        if status:
            q = q.where(Issue.status == status)
        if level:
            q = q.where(Issue.level == level)
        if environment:
            q = q.where(Issue.last_environment == environment)
        if release:
            q = q.where(or_(Issue.last_release == release, Issue.first_release == release))
        total = session.scalar(select(func.count()).select_from(q.subquery()))
        order = {"count": Issue.count.desc(), "first_seen": Issue.first_seen.desc()}.get(sort, Issue.last_seen.desc())
        rows = session.scalars(q.order_by(order).limit(min(limit, 200)).offset(offset)).all()
        sp = sparklines(session, [i.id for i in rows])
        return {"total": total, "items": [issue_json(i, sp[i.id]) for i in rows]}

    @router.get("/projects/{pid}/releases", dependencies=dep)
    def list_releases(pid: int, session=Depends(db)):
        project_or_404(session, pid)
        rows = session.scalars(select(Release).where(Release.project_id == pid).order_by(Release.first_seen.desc()))
        envs = session.scalars(select(Issue.last_environment).where(Issue.project_id == pid).distinct()).all()
        return {"releases": [{"version": r.version, "first_seen": _iso(r.first_seen),
                              "event_count": r.event_count} for r in rows],
                "environments": sorted(e for e in envs if e)}

    def get_issue(session, iid):
        i = session.get(Issue, iid)
        if not i:
            raise HTTPException(404, "issue not found")
        return i

    def event_json(e):
        return {"id": e.id, "event_id": e.event_id, "timestamp": _iso(e.timestamp),
                "release": e.release, "environment": e.environment, **{
                    k: e.data.get(k) for k in ("level", "message", "platform", "exception", "breadcrumbs",
                                               "tags", "user", "request", "sdk")}}

    @router.get("/issues/{iid}", dependencies=dep)
    def issue_detail(iid: int, session=Depends(db)):
        i = get_issue(session, iid)
        latest = session.scalar(select(Event).where(Event.issue_id == iid).order_by(Event.timestamp.desc()))
        return {**issue_json(i, sparklines(session, [iid])[iid]),
                "latest_event": event_json(latest) if latest else None}

    @router.patch("/issues/{iid}", dependencies=dep)
    def patch_issue(iid: int, body: IssuePatch, session=Depends(db)):
        i = get_issue(session, iid)
        i.status = body.status
        session.commit()
        return issue_json(i)

    @router.get("/issues/{iid}/events", dependencies=dep)
    def issue_events(iid: int, limit: int = 25, offset: int = 0, session=Depends(db)):
        get_issue(session, iid)
        rows = session.scalars(select(Event).where(Event.issue_id == iid).order_by(Event.timestamp.desc())
                               .limit(min(limit, 100)).offset(offset))
        return [event_json(e) for e in rows]

    @router.post("/projects/{pid}/releases/{release}/sourcemaps", dependencies=dep, status_code=201)
    async def upload_sourcemap(pid: int, release: str, filename: str = Form(...), file: UploadFile = File(...),
                               session=Depends(db)):
        project_or_404(session, pid)
        raw = (await file.read()).decode("utf-8")
        try:
            SourceMapIndex(raw)
        except Exception as e:
            raise HTTPException(422, f"invalid source map: {e}")
        key = filename.split("?")[0].rsplit("/", 1)[-1]
        row = session.scalar(select(SourceMap).where(SourceMap.project_id == pid, SourceMap.release == release,
                                                     SourceMap.filename == key))
        if row:
            row.content = raw
        else:
            session.add(SourceMap(project_id=pid, release=release, filename=key, content=raw))
        session.commit()
        return {"release": release, "filename": key, "bytes": len(raw)}

    def rule_json(r):
        return {"id": r.id, "project_id": r.project_id, "kind": r.kind, "threshold": r.threshold,
                "window_seconds": r.window_seconds, "webhook_url": r.webhook_url, "last_fired": _iso(r.last_fired)}

    @router.get("/projects/{pid}/alert-rules", dependencies=dep)
    def list_rules(pid: int, session=Depends(db)):
        project_or_404(session, pid)
        return [rule_json(r) for r in session.scalars(select(AlertRule).where(AlertRule.project_id == pid))]

    @router.post("/projects/{pid}/alert-rules", dependencies=dep, status_code=201)
    def create_rule(pid: int, body: RuleIn, session=Depends(db)):
        project_or_404(session, pid)
        r = AlertRule(project_id=pid, **body.model_dump())
        session.add(r)
        session.commit()
        return rule_json(r)

    @router.delete("/alert-rules/{rid}", dependencies=dep, status_code=204)
    def delete_rule(rid: int, session=Depends(db)):
        session.execute(delete(AlertRule).where(AlertRule.id == rid))
        session.commit()

    @router.get("/projects/{pid}/alerts", dependencies=dep)
    def alert_log(pid: int, limit: int = 50, session=Depends(db)):
        project_or_404(session, pid)
        q = (select(AlertLog, AlertRule.kind).join(AlertRule, AlertRule.id == AlertLog.rule_id)
             .where(AlertRule.project_id == pid).order_by(AlertLog.fired_at.desc()).limit(min(limit, 200)))
        return [{"id": a.id, "rule_id": a.rule_id, "kind": kind, "issue_id": a.issue_id,
                 "fired_at": _iso(a.fired_at), "delivered": a.delivered, "detail": a.detail}
                for a, kind in session.execute(q)]

    return router
