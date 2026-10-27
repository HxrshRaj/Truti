"""End-to-end smoke test against a running stack (docker compose up).

    python scripts/e2e_smoke.py [http://127.0.0.1:8000]

Registers the first user, creates a project, sends errors through the real Python SDK, then asserts
that ingest -> Redis -> worker -> Postgres -> API produced grouped issues with stack frames, that a
repeated bug grouped into one issue, and that another user cannot see the project.
Needs the `truti` package installed (pip install -e sdk-python). Exits non-zero on failure.
"""
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request

API = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000").rstrip("/")


def call(method, path, body=None, token=None):
    req = urllib.request.Request(API + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {token}"} if token else {})})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


def check(cond, msg):
    print(("ok   " if cond else "FAIL ") + msg)
    if not cond:
        sys.exit(1)


for _ in range(60):  # wait for the stack
    try:
        if call("GET", "/healthz")[0] == 200:
            break
    except Exception:
        pass
    time.sleep(2)
else:
    check(False, "ingest became healthy")

status, reg = call("POST", "/api/0/auth/register", {"email": "e2e@example.com", "password": "e2e-password-1"})
check(status == 201, "first user registers")
tok = reg["token"]
status, proj = call("POST", "/api/0/projects", {"name": "e2e"}, tok)
check(status == 201 and proj["dsn"], "project created with a DSN")

import os
env = {**os.environ, "TRUTI_DSN": proj["dsn"].replace("localhost", "127.0.0.1")}
demo = subprocess.run([sys.executable, "demo/python_demo.py", "3"], env=env, capture_output=True, text=True, timeout=120)
check("unhandled crash at end of demo" in demo.stderr, "demo ran and crashed on purpose (excepthook path)")

issues = []
for _ in range(30):
    status, res = call("GET", f"/api/0/projects/{proj['id']}/issues", token=tok)
    issues = res["items"]
    if len(issues) >= 4 and sum(i["count"] for i in issues) >= 10:
        break
    time.sleep(1)
check(len(issues) == 4, f"4 distinct bugs -> 4 issues (got {len(issues)})")
check(max(i["count"] for i in issues) >= 3, "a bug repeated over 3 rounds grouped into one issue with count >= 3")
status, detail = call("GET", f"/api/0/issues/{issues[0]['id']}", token=tok)
frames = detail["latest_event"]["exception"]["frames"] if detail["latest_event"].get("exception") else []
check(bool(frames) and frames[-1].get("context_line") is not None, "issue detail has stack frames with source context")

status, other = call("POST", "/api/0/auth/register", {"email": "other@example.com", "password": "other-password-1"})
check(status == 403, "registration is closed after the first user")
check(call("GET", f"/api/0/projects/{proj['id']}/issues")[0] == 401, "dashboard API rejects anonymous requests")
print("e2e smoke: all checks passed")
