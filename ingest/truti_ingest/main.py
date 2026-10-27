"""Ingest HTTP service: envelope endpoint + metrics + dashboard API."""
import json
import time
import zlib

import redis.asyncio as aioredis
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from . import metrics as m
from .api import make_router
from .config import Settings
from .db import Project, make_session_factory
from .schema import Envelope

_PROJECT_TTL = 30.0


def _err(status, msg, details=None, headers=None):
    body = {"error": msg}
    if details is not None:
        body["details"] = details
    return JSONResponse(body, status_code=status, headers=headers)


def create_app(settings: Settings | None = None, redis_client=None, session_factory=None) -> FastAPI:
    s = settings or Settings()
    Session = session_factory or make_session_factory(s.database_url, use_migrations=True)
    r = redis_client or aioredis.Redis.from_url(s.redis_url, decode_responses=True)
    app = FastAPI(title="Truti ingest")
    app.add_middleware(CORSMiddleware, allow_origins=s.cors_origins.split(","), allow_methods=["*"],
                       allow_headers=["*"], expose_headers=["Retry-After"])
    cache: dict[int, tuple[float, str, int | None]] = {}

    def lookup_project(pid):
        with Session() as db:
            p = db.get(Project, pid)
            return (p.public_key, p.rate_limit_eps) if p else None

    async def project_info(pid):
        hit = cache.get(pid)
        if hit and hit[0] > time.monotonic():
            return hit[1], hit[2]
        info = await run_in_threadpool(lookup_project, pid)
        if info is None:
            cache.pop(pid, None)
            return None
        cache[pid] = (time.monotonic() + _PROJECT_TTL, *info)
        return info

    def reject(reason, status, msg, details=None, headers=None, n=1):
        m.EVENTS_REJECTED.labels(reason).inc(n)
        return _err(status, msg, details, headers)

    @app.post("/api/{project_id}/envelope/")
    async def envelope(project_id: int, request: Request):
        t0 = time.perf_counter()
        try:
            return await _envelope(project_id, request)
        finally:
            m.REQUEST_LATENCY.observe(time.perf_counter() - t0)

    async def _envelope(project_id, request):
        # 1. auth
        auth = request.headers.get("x-truti-auth", "")
        key = auth.split("truti_key=", 1)[1].split(",")[0].strip() if "truti_key=" in auth \
            else request.query_params.get("truti_key")
        info = await project_info(project_id)
        if not key or info is None or key != info[0]:
            return reject("auth", 401, "invalid project or public key")
        # 2. size
        cl = request.headers.get("content-length")
        if cl and cl.isdigit() and int(cl) > s.max_body_bytes:
            return reject("too_large", 413, f"body exceeds {s.max_body_bytes} bytes")
        body = await request.body()
        if len(body) > s.max_body_bytes:
            return reject("too_large", 413, f"body exceeds {s.max_body_bytes} bytes")
        # 3. decompress (bounded, so a tiny gzip bomb cannot expand past the size limit) + parse + validate
        enc = request.headers.get("content-encoding", "").lower()
        if enc == "gzip":
            try:
                d = zlib.decompressobj(wbits=31)
                body = d.decompress(body, s.max_body_bytes + 1)
            except zlib.error:
                return reject("bad_gzip", 400, "body is not valid gzip")
            if len(body) > s.max_body_bytes or d.unconsumed_tail:
                return reject("too_large", 413, f"decompressed body exceeds {s.max_body_bytes} bytes")
        elif enc not in ("", "identity"):
            return reject("bad_encoding", 415, f"unsupported Content-Encoding {enc}")
        try:
            payload = json.loads(body)
        except ValueError:
            return reject("bad_json", 400, "body is not valid JSON")
        try:
            env = Envelope.model_validate(payload)
        except ValidationError as e:
            details = [{"loc": ".".join(map(str, x["loc"])), "msg": x["msg"]} for x in e.errors()[:20]]
            return reject("schema", 422, "envelope failed validation", details)
        n = len(env.events)
        # 4. backpressure
        if await r.xlen(s.stream) >= s.stream_maxlen:
            return reject("queue_full", 503, "queue full, retry later", headers={"Retry-After": "5"}, n=n)
        # 5. per-project rate limit (fixed 1s window, counts events)
        limit = info[1] or s.default_rate_limit_eps
        now = time.time()
        rk = f"rate:{project_id}:{int(now)}"
        used = await r.incrby(rk, n)
        if used == n:
            await r.expire(rk, 2)
        if used > limit:
            retry = max(1, int(int(now) + 1 - now + 0.999))
            return reject("rate_limited", 429, "rate limit exceeded", headers={"Retry-After": str(retry)}, n=n)
        # 6. dedup + enqueue
        pipe = r.pipeline(transaction=False)
        for ev in env.events:
            pipe.set(f"dedup:{project_id}:{ev.event_id}", 1, nx=True, ex=s.dedup_ttl_seconds)
        fresh = await pipe.execute()
        events = [ev for ev, ok in zip(env.events, fresh) if ok]
        dups = n - len(events)
        if dups:
            m.EVENTS_DUPLICATE.inc(dups)
        if events:
            pipe = r.pipeline(transaction=False)
            for ev in events:
                pipe.xadd(s.stream, {"project_id": project_id, "received_at": f"{now:.6f}",
                                     "data": ev.model_dump_json(exclude_none=True)},
                          maxlen=s.stream_maxlen * 2, approximate=True)
            await pipe.execute()
            m.EVENTS_QUEUED.inc(len(events))
        m.EVENTS_RECEIVED.inc(n)
        return JSONResponse({"accepted": len(events), "duplicates": dups}, status_code=202)

    @app.get("/metrics")
    async def metrics():
        try:
            m.QUEUE_DEPTH.set(await r.xlen(s.stream))
        except Exception:
            pass
        return Response(generate_latest(m.REGISTRY), media_type=CONTENT_TYPE_LATEST)

    @app.get("/healthz")
    async def health():
        await r.ping()
        return {"ok": True}

    app.include_router(make_router(s, Session), prefix="/api/0")
    return app


def app_factory():  # uvicorn truti_ingest.main:app_factory --factory
    return create_app()
