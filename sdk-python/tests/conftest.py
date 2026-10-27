import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import truti
from truti import client as client_mod
from truti.scope import reset_global_scope


class FakeServer:
    def __init__(self):
        self.received = []   # list of (headers, envelope)
        self.script = []     # queued (status, headers, delay) responses; default 202
        self.default = (202, {}, 0)
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                status, headers, delay = outer.script.pop(0) if outer.script else outer.default
                if delay:
                    time.sleep(delay)
                if 200 <= status < 300:
                    outer.received.append((dict(self.headers), json.loads(body)))
                self.send_response(status)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *a):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    @property
    def dsn(self):
        return f"http://pubkey123@127.0.0.1:{self.port}/7"

    @property
    def events(self):
        return [e for _, env in self.received for e in env["events"]]

    def stop(self):
        self.httpd.shutdown()


@pytest.fixture
def server():
    s = FakeServer()
    yield s
    s.stop()


@pytest.fixture(autouse=True)
def clean_state():
    reset_global_scope()
    yield
    if client_mod._client is not None:
        client_mod._client.transport._stop.set()
    client_mod._client = None
    reset_global_scope()


@pytest.fixture
def init_sdk(server):
    def _init(**kw):
        kw.setdefault("flush_interval", 0.05)
        kw.setdefault("base_delay", 0.02)
        kw.setdefault("auto_hooks", False)
        return truti.init(server.dsn, **kw)
    return _init
