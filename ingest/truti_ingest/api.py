"""Dashboard API, mounted at /api/0.

Auth: ``Authorization: Bearer <token>`` where token is a user session (from /auth/login) or the
bootstrap ``ADMIN_TOKEN``. Users only see projects they are members of; admins see everything.
"""
import datetime as dt

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, or_, select, update

from . import auth
from .db import (AlertLog, AlertRule, Event, FingerprintAlias, Issue, Membership, Project, Release,
                 SourceMap, User, UserSession, utcnow)
from .symbolicate import SourceMapIndex

SPARK_BUCKETS = 24


def _iso(d):
    if d is None:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return d.isoformat().replace("+00:00", "Z")


def _naive(d):
    return d.replace(tzinfo=None) if d.tzinfo else d


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class IssuePatch(BaseModel):
    status: str = Field(pattern="^(unresolved|resolved|ignored)$")


class MergeIn(BaseModel):
    into: int


class RuleIn(BaseModel):
    kind: str = Field(pattern="^(new_issue|spike)$")
    channel: str = Field(default="webhook", pattern="^(webhook|email)$")
    webhook_url: str = Field(default="", max_length=2000)
    email_to: str | None = Field(default=None, max_length=320, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    threshold: int = Field(default=10, ge=1, le=100000)
    window_seconds: int = Field(default=60, ge=1, le=86400)


class Credentials(BaseModel):
    email: str = Field(max_length=320, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    password: str = Field(min_length=8, max_length=200)


class MemberIn(BaseModel):
    email: str
    role: str = Field(default="member", pattern="^(owner|member)$")


def make_router(s, Session):
    router = APIRouter()
    throttle = auth.LoginThrottle()

    def db():
        with Session() as session:
            yield session

    def principal(request: Request, session=Depends(db)) -> auth.Principal:
        return auth.authenticate(request, session, s.admin_token)

    P = Depends(principal)

    def need(session, who, pid, owner=False):
        """404 for projects you cannot see (ids do not leak); 403 if you lack the owner role."""
        p = session.get(Project, pid)
        role = auth.role_in(session, who, pid) if p else None
        if not p or role is None:
            raise HTTPException(404, "project not found")
        if owner and role != "owner":
            raise HTTPException(403, "owner role required")
        return p

    # ---- accounts -----------------------------------------------------------------------------
    def user_json(u):
        return {"id": u.id, "email": u.email, "is_admin": u.is_admin}

    @router.post("/auth/register", status_code=201)
    def register(body: Credentials, session=Depends(db)):
        first = session.scalar(select(func.count()).select_from(User)) == 0
        if not first and not s.allow_registration:
            raise HTTPException(403, "registration is closed; ask an owner to create your account")
        email = body.email.lower()
        if session.scalar(select(User).where(User.email == email)):
            raise HTTPException(409, "email already registered")
        u = User(email=email, password_hash=auth.hash_password(body.password), is_admin=first)
        session.add(u)
        session.commit()
        return {"token": auth.create_session(session, u.id), "user": user_json(u)}

    @router.post("/auth/login")
    def login(body: Credentials, request: Request, session=Depends(db)):
        key = f"{body.email.lower()}|{request.client.host if request.client else '?'}"
        throttle.check(key)
        u = session.scalar(select(User).where(User.email == body.email.lower()))
        if not u or not auth.verify_password(body.password, u.password_hash):
            throttle.fail(key)
            raise HTTPException(401, "wrong email or password")
        return {"token": auth.create_session(session, u.id), "user": user_json(u)}

    @router.post("/auth/logout", status_code=204)
    def logout(request: Request, who=P, session=Depends(db)):
        tok = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        session.execute(delete(UserSession).where(UserSession.token_hash == auth._tok_hash(tok)))
        session.commit()

    @router.get("/auth/me")
    def me(who=P):
        return {"user": user_json(who.user) if who.user else None, "is_admin": who.is_admin}

    @router.get("/projects/{pid}/members")
    def members(pid: int, who=P, session=Depends(db)):
        need(session, who, pid)
        q = select(User.email, Membership.role).join(Membership, Membership.user_id == User.id).where(
            Membership.project_id == pid)
        return [{"email": e, "role": r} for e, r in session.execute(q)]

    @router.post("/projects/{pid}/members", status_code=201)
    def add_member(pid: int, body: MemberIn, who=P, session=Depends(db)):
        need(session, who, pid, owner=True)
        u = session.scalar(select(User).where(User.email == body.email.lower()))
        if not u:
            raise HTTPException(404, "no such user (they must register first)")
        m = session.scalar(select(Membership).where(Membership.user_id == u.id, Membership.project_id == pid))
        if m:
            m.role = body.role
        else:
            session.add(Membership(user_id=u.id, project_id=pid, role=body.role))
        session.commit()
        return {"email": u.email, "role": body.role}

    # ---- projects -------------------------------------------------------------------------------
    def project_json(p, request):
        host = request.headers.get("host", request.url.netloc)
        scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
        return {"id": p.id, "name": p.name, "public_key": p.public_key,
                "dsn": f"{scheme}://{p.public_key}@{host}/{p.id}", "created_at": _iso(p.created_at)}

    @router.get("/projects")
    def list_projects(request: Request, who=P, session=Depends(db)):
        q = select(Project).order_by(Project.id)
        ids = auth.project_ids_for(session, who)
        if ids is not None:
            q = q.where(Project.id.in_(ids))
        return [project_json(p, request) for p in session.scalars(q)]

    @router.post("/projects", status_code=201)
    def create_project(body: ProjectIn, request: Request, who=P, session=Depends(db)):
        p = Project(name=body.name)
        session.add(p)
        session.flush()
        if who.user:
            session.add(Membership(user_id=who.user.id, project_id=p.id, role="owner"))
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

    @router.get("/projects/{pid}/issues")
    def list_issues(pid: int, query: str = "", status: str = "", environment: str = "", release: str = "",
                    level: str = "", sort: str = "last_seen", limit: int = 50, offset: int = 0,
                    who=P, session=Depends(db)):
        need(session, who, pid)
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

    @router.get("/projects/{pid}/releases")
    def list_releases(pid: int, who=P, session=Depends(db)):
        need(session, who, pid)
        rows = session.scalars(select(Release).where(Release.project_id == pid).order_by(Release.first_seen.desc()))
        envs = session.scalars(select(Issue.last_environment).where(Issue.project_id == pid).distinct()).all()
        return {"releases": [{"version": r.version, "first_seen": _iso(r.first_seen),
                              "event_count": r.event_count} for r in rows],
                "environments": sorted(e for e in envs if e)}

    def get_issue(session, who, iid):
        i = session.get(Issue, iid)
        if i is not None:
            try:
                need(session, who, i.project_id)
            except HTTPException:
                i = None
        if i is None:
            raise HTTPException(404, "issue not found")
        return i

    def event_json(e):
        return {"id": e.id, "event_id": e.event_id, "timestamp": _iso(e.timestamp),
                "release": e.release, "environment": e.environment, **{
                    k: e.data.get(k) for k in ("level", "message", "platform", "exception", "breadcrumbs",
                                               "tags", "user", "request", "sdk")}}

    @router.get("/issues/{iid}")
    def issue_detail(iid: int, who=P, session=Depends(db)):
        i = get_issue(session, who, iid)
        latest = session.scalar(select(Event).where(Event.issue_id == iid).order_by(Event.timestamp.desc()))
        return {**issue_json(i, sparklines(session, [iid])[iid]),
                "latest_event": event_json(latest) if latest else None}

    @router.patch("/issues/{iid}")
    def patch_issue(iid: int, body: IssuePatch, who=P, session=Depends(db)):
        i = get_issue(session, who, iid)
        i.status = body.status
        session.commit()
        return issue_json(i)

    @router.post("/issues/{iid}/merge")
    def merge_issue(iid: int, body: MergeIn, who=P, session=Depends(db)):
        """Merge issue ``iid`` into ``body.into`` (same project). Events move, counts add, and the
        source fingerprint becomes an alias so future events with it land in the target."""
        src = get_issue(session, who, iid)
        dst = get_issue(session, who, body.into)
        if src.id == dst.id or src.project_id != dst.project_id:
            raise HTTPException(422, "can only merge two different issues of the same project")
        session.execute(update(Event).where(Event.issue_id == src.id).values(issue_id=dst.id))
        session.execute(update(FingerprintAlias).where(FingerprintAlias.issue_id == src.id).values(issue_id=dst.id))
        dst.count += src.count
        dst.first_seen = min(dst.first_seen, src.first_seen, key=_naive)
        dst.last_seen = max(dst.last_seen, src.last_seen, key=_naive)
        session.add(FingerprintAlias(project_id=src.project_id, fingerprint=src.fingerprint, issue_id=dst.id))
        session.delete(src)
        session.commit()
        return issue_json(dst)

    @router.get("/issues/{iid}/events")
    def issue_events(iid: int, limit: int = 25, offset: int = 0, who=P, session=Depends(db)):
        get_issue(session, who, iid)
        rows = session.scalars(select(Event).where(Event.issue_id == iid).order_by(Event.timestamp.desc())
                               .limit(min(limit, 100)).offset(offset))
        return [event_json(e) for e in rows]

    @router.post("/projects/{pid}/releases/{release}/sourcemaps", status_code=201)
    async def upload_sourcemap(pid: int, release: str, filename: str = Form(...), file: UploadFile = File(...),
                               who=P, session=Depends(db)):
        need(session, who, pid)
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
                "window_seconds": r.window_seconds, "channel": r.channel, "webhook_url": r.webhook_url,
                "email_to": r.email_to, "last_fired": _iso(r.last_fired)}

    @router.get("/projects/{pid}/alert-rules")
    def list_rules(pid: int, who=P, session=Depends(db)):
        need(session, who, pid)
        return [rule_json(r) for r in session.scalars(select(AlertRule).where(AlertRule.project_id == pid))]

    @router.post("/projects/{pid}/alert-rules", status_code=201)
    def create_rule(pid: int, body: RuleIn, who=P, session=Depends(db)):
        need(session, who, pid)
        if body.channel == "webhook" and not body.webhook_url.startswith(("http://", "https://")):
            raise HTTPException(422, "webhook rules need an http(s) webhook_url")
        if body.channel == "email" and not body.email_to:
            raise HTTPException(422, "email rules need email_to")
        r = AlertRule(project_id=pid, **body.model_dump())
        session.add(r)
        session.commit()
        return rule_json(r)

    @router.delete("/alert-rules/{rid}", status_code=204)
    def delete_rule(rid: int, who=P, session=Depends(db)):
        r = session.get(AlertRule, rid)
        if r:
            need(session, who, r.project_id)
            session.delete(r)
            session.commit()

    @router.get("/projects/{pid}/alerts")
    def alert_log(pid: int, limit: int = 50, who=P, session=Depends(db)):
        need(session, who, pid)
        q = (select(AlertLog, AlertRule.kind).join(AlertRule, AlertRule.id == AlertLog.rule_id)
             .where(AlertRule.project_id == pid).order_by(AlertLog.fired_at.desc()).limit(min(limit, 200)))
        return [{"id": a.id, "rule_id": a.rule_id, "kind": kind, "issue_id": a.issue_id,
                 "fired_at": _iso(a.fired_at), "delivered": a.delivered, "detail": a.detail}
                for a, kind in session.execute(q)]

    return router
