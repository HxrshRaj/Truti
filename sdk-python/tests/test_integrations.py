import asyncio

import pytest

import truti
from truti.integrations.asgi import TrutiASGIMiddleware


async def test_asyncio_tasks_capture_without_blocking(server, init_sdk):
    init_sdk()

    async def worker(i):
        await asyncio.sleep(0)
        truti.capture_message(f"task {i}")

    await asyncio.gather(*(worker(i) for i in range(10)))
    assert await asyncio.get_running_loop().run_in_executor(None, truti.flush, 3)
    assert len(server.events) == 10


async def test_push_scope_isolated_between_tasks(server, init_sdk):
    init_sdk()

    async def req(i):
        with truti.push_scope():
            truti.set_tag("req", str(i))
            await asyncio.sleep(0.01)
            truti.capture_message("m")

    await asyncio.gather(req(1), req(2))
    await asyncio.get_running_loop().run_in_executor(None, truti.flush, 3)
    assert sorted(e["tags"]["req"] for e in server.events) == ["1", "2"]


def test_fastapi_middleware_captures_and_reraises(server, init_sdk):
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    init_sdk()
    app = FastAPI()

    @app.get("/boom")
    def boom():
        raise RuntimeError("route failed")

    @app.get("/ok")
    def ok():
        return {"ok": True}

    app.add_middleware(TrutiASGIMiddleware)
    client = TestClient(app, raise_server_exceptions=False)
    assert client.get("/ok").status_code == 200
    r = client.get("/boom", headers={"Authorization": "Bearer secretsecret1"})
    assert r.status_code == 500
    truti.flush(3)
    assert len(server.events) == 1
    ev = server.events[0]
    assert ev["exception"]["type"] == "RuntimeError"
    assert ev["request"]["url"] == "/boom"
    assert ev["request"]["headers"]["authorization"] == "[Filtered]"
    assert any(b["category"] == "http" for b in ev["breadcrumbs"])


def test_flask_integration(server, init_sdk):
    from flask import Flask
    from truti.integrations.flask import init_app

    init_sdk()
    app = Flask(__name__)
    init_app(app)

    @app.route("/boom")
    def boom():
        raise KeyError("missing")

    c = app.test_client()
    assert c.get("/boom").status_code == 500
    truti.flush(3)
    assert [e["exception"]["type"] for e in server.events] == ["KeyError"]
    assert server.events[0]["request"]["url"] == "/boom"
