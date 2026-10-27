import datetime as dt
import uuid

import fakeredis
import pytest
from fastapi.testclient import TestClient

from truti_ingest.config import Settings
from truti_ingest.db import Project, make_session_factory
from truti_ingest.main import create_app
from truti_ingest.worker import ensure_group, run_once

ADMIN = {"Authorization": "Bearer test-admin"}


def make_event(**over):
    ev = {
        "event_id": uuid.uuid4().hex,
        "timestamp": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "platform": "python", "level": "error", "release": "1.0.0", "environment": "prod",
        "exception": {"type": "ValueError", "value": "bad 42", "frames": [
            {"filename": "app/main.py", "function": "handler", "lineno": 10, "context_line": "x = int(s)", "in_app": True},
            {"filename": "app/util.py", "function": "parse", "lineno": 3, "context_line": "raise ValueError", "in_app": True}]},
    }
    ev.update(over)
    return ev


def envelope(*events):
    return {"envelope_version": 1, "sent_at": "2026-01-01T00:00:00Z", "events": list(events)}


class Env:
    def __init__(self, **settings):
        from truti_ingest import alerts, processing
        alerts.clear_cache(); processing._KNOWN_RELEASES.clear()
        self.settings = Settings(database_url="sqlite://", admin_token="test-admin", **settings)
        self.server = fakeredis.FakeServer()
        self.sync_redis = fakeredis.FakeRedis(server=self.server, decode_responses=True)
        self.async_redis = fakeredis.FakeAsyncRedis(server=self.server, decode_responses=True)
        self.Session = make_session_factory("sqlite://")
        self.app = create_app(self.settings, self.async_redis, self.Session)
        self.client = TestClient(self.app)
        ensure_group(self.sync_redis, self.settings)
        with self.Session() as s:
            p = Project(name="demo")
            s.add(p)
            s.commit()
            self.pid, self.key = p.id, p.public_key
        self.posted = []

    def send(self, *events, key=None, **kw):
        return self.client.post(f"/api/{self.pid}/envelope/", json=envelope(*events),
                                headers={"X-Truti-Auth": f"truti_key={key or self.key}"}, **kw)

    def drain(self):
        total = 0
        while True:
            n = run_once(self.sync_redis, self.settings, self.Session, "t1", block_ms=1,
                         post=self._post)
            total += n
            if n == 0:
                return total

    def _post(self, url, json=None, timeout=None):
        self.posted.append((url, json))

        class R:
            status_code = 200
        return R()

    def api(self, method, path, **kw):
        return self.client.request(method, "/api/0" + path, headers=ADMIN, **kw)


@pytest.fixture
def env():
    return Env()
