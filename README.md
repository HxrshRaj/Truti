# Truti

*Truti* means "error" in Hindi. It is a small, honest version of what Sentry does: SDKs capture errors,
an ingest service validates and queues them, a worker groups them into issues, and a web app shows them.

**Status:** everything below runs locally via Docker Compose, passes its tests (102), and is exercised end to end
by `scripts/e2e_smoke.py` and by a real browser error. It is **not deployed**: `render.yaml` is written but has
never been applied (see [Limits](#limits-and-what-is-not-done)).

## Architecture

```
 Python SDK ─┐                                   ┌────────────┐
 JS/TS SDK  ─┼─ POST /api/{project}/envelope/ ─▶ │  ingest    │── dedup / rate limit / validate
 (browser,   │      X-Truti-Auth: truti_key=…    │  (FastAPI) │
  node)     ─┘                                   └─────┬──────┘
                                                       │ XADD
                                                 ┌─────▼──────┐
                                                 │ Redis      │  stream truti:events
                                                 │ Streams    │  (consumer group, dead-letter stream)
                                                 └─────┬──────┘
                                                       │ XREADGROUP
                                                 ┌─────▼──────┐   symbolicate → fingerprint → upsert issue
                                                 │  worker    │── insert event → release tracking → alerts
                                                 └─────┬──────┘
                                                       │
                          ┌────────────┐         ┌─────▼──────┐
                          │ web (React)│─ /api/0 ▶│ PostgreSQL │
                          └────────────┘  (ingest)└────────────┘
```

| Directory | What |
|---|---|
| `sdk-python/` | Python SDK: `init`, `capture_exception/message`, scopes, breadcrumbs, excepthook + threading hook, asyncio-safe, ASGI/FastAPI, Flask and `logging` integrations, secret scrubbing, batching background transport |
| `sdk-js/` | TypeScript SDK for Node and browsers: `init`, `captureException`, `window.onerror`/`unhandledrejection`, Express middleware, same retry/flush behaviour |
| `ingest/` | FastAPI ingest API, Redis Streams worker, Postgres models, dashboard API (`/api/0`), accounts + project membership, source-map symbolication, webhook and email alerts, issue merge, retention, Alembic migrations, Prometheus metrics, load test |
| `web/` | React + Vite dashboard: projects/DSN, issues list (sparkline, filters, search), issue detail (stack frames with source context, breadcrumbs, tags, release, user), alert rules |
| `scripts/e2e_smoke.py` | End-to-end check against a running stack (also a CI job) |
| `demo/` | Deliberately broken Python app and a minified browser bundle with a source map |
| `docs/envelope.md` | The versioned envelope format |

## Run it locally

```bash
docker compose up --build            # dashboard :8080, ingest/API :8000, admin token dev-admin-token
curl -XPOST localhost:8000/api/0/projects -H "Authorization: Bearer dev-admin-token" \
     -H 'content-type: application/json' -d '{"name":"demo"}'      # returns the DSN
TRUTI_DSN=<dsn> python demo/python_demo.py 2                       # sends real errors
```

Verified on this machine: the demo's four distinct bugs, run for two rounds, produced four issues in the
API, with the repeated ones at count 2; the dashboard container serves on :8080 and proxies `/api`.

Sign in at http://localhost:8080 (the first account you create becomes the admin; registration then closes
unless `ALLOW_REGISTRATION=1`). `ADMIN_TOKEN` still works as a bootstrap credential for scripts.

Tests (CI runs the same in `.github/workflows/ci.yml`; the workflow itself has not run on GitHub yet):

```bash
cd sdk-python && pip install -e ".[test]" && pytest     # 28 tests
cd ingest     && pip install -e ".[test]" && pytest     # 51 tests
cd sdk-js     && npm ci && npm test                     # 17 tests (vitest)
cd web        && npm ci && npm test && npm run build    # 6 tests (vitest)
docker compose up --build -d && python scripts/e2e_smoke.py   # end to end, 8 checks
```

All 102 unit tests and the 8-check smoke test passed locally. I also drove the browser demo in a real
browser against the stack: a `TypeError` thrown by the minified bundle arrived as an issue whose frames were
symbolicated back to `app.ts` with original source lines, and the dashboard login and issue list rendered.

## How grouping works

Defined in `ingest/truti_ingest/grouping.py`. The fingerprint is a SHA-1 over:

* **Exception events:** the exception *type*, plus the last 5 **in-app** frames (all frames if none are
  in-app), each as `(filename, function, stripped source line)`. Line numbers and the exception
  *message* are deliberately excluded, so editing code above a frame, or `KeyError: 7` vs `KeyError: 9`,
  does not create a new issue. A different exception type or a different call path does.
* **Message events:** the message with digits, hex, UUIDs and quoted strings replaced by placeholders.

Same fingerprint → same issue: the worker bumps `count`, `last_seen`, `last_release`, and moves
`first_seen` earlier if an older event arrives late. A resolved issue that receives a new event reopens.
Redelivered stream messages do not double-count (unique `(project, event_id)` row).

## Source maps

`ingest/truti_ingest/symbolicate.py` decodes source map v3 itself (base64 VLQ, delta-decoded) and, per
minified frame, binary-searches the generated line's segments for the last one at or before the column
(greatest lower bound). It then swaps in the original file, line, column, symbol name and source context
(from `sourcesContent`). Maps are uploaded per project, release and minified file name
(`POST /api/0/projects/{id}/releases/{release}/sourcemaps`, see `demo/upload_sourcemap.py`); events are
symbolicated by the worker when stored, matched on release and file basename. Covered by tests with a
hand-built map, and end to end with the demo's esbuild-minified bundle and its real map (see above).

## Reliability and limits of the pipeline

* SDKs never raise into the host app, bound their queue, retry 5xx/429/network errors with exponential
  backoff and full jitter, honour `Retry-After`, and drop (not retry) other 4xx. `flush(timeout)` blocks
  until drained or timed out.
* Ingest: 401 bad key, 413 over `MAX_BODY_BYTES` (200 KB), 400 bad JSON, 422 schema errors with field
  paths, 429 + `Retry-After` per-project rate limit (fixed 1 s window, default 200 events/s), 503 +
  `Retry-After` when the stream is full, de-dup by event id (1 h window).
* Worker: at-least-once delivery, one database transaction per batch of up to 200 messages. If a batch fails
  it is retried event by event so one bad event cannot block others; unparseable messages are dead-lettered
  immediately and others after 3 deliveries; crashed consumers' messages are reclaimed via `XAUTOCLAIM`.
  Alerts are delivered after commit, so a slow webhook or SMTP server never holds a transaction.
* Retention: the worker deletes events older than `RETENTION_DAYS` (default 30) hourly, then issues left
  with no events.
* Ingest accepts `Content-Encoding: gzip` (the Python SDK gzips batches over 1 KB); decompression is capped,
  so a small gzip bomb gets a 413.
* Schema is managed by Alembic (`truti_ingest/migrations`); services migrate on boot under a Postgres
  advisory lock, and a test asserts the migrations produce exactly the schema the models define.
* Accounts: scrypt-hashed passwords, opaque session tokens (only their SHA-256 is stored), login throttling,
  and per-project membership (owner/member). Non-members get 404 for a project's issues, rules and maps.
* Metrics at `/metrics` (ingest) and `:9100/metrics` (worker): received, rejected by reason, queued,
  duplicates, processed, failed, request/processing latency, queue wait, queue length.

## Load test: real numbers

Full detail and the exact setup are in [`ingest/loadtest/README.md`](ingest/loadtest/README.md). Docker
Desktop on a Windows laptop (12 vCPU), Postgres + Redis + 1 ingest container (2 uvicorn workers) +
**1 worker**, with the load generator on the same machine. Per-project rate limit raised for the test.

```bash
python loadtest.py --url http://ingest:8000 --project ID --key KEY \
       --duration 20 --processes 8 --concurrency 4 --batch 1     # run in a container on the compose network
```

| run (20 s, 32 connections) | events accepted/s | p50 | p95 | p99 | not accepted |
|---|---|---|---|---|---|
| 1 event / envelope | 623 | 47 ms | 86 ms | 128 ms | 0 of 12,557 (0.00%) |
| 10 events / envelope | 3,163 | 90 ms | 175 ms | 234 ms | 0 of 64,130 (0.00%) |

What this says, honestly:

* **No loss, no backpressure.** All 76,687 accepted events were stored (76,687 distinct ids in Postgres),
  0 dead-lettered, 0 rejected with 503.
* **The worker was the bottleneck, and batching fixed most of it.** The first version committed once per
  event and stored about 90-100 events/s; with that worker the same batch-10 run got 46% HTTP 503 once the
  queue filled (details of that older run are in the loadtest README).
  With one transaction per batch, a 40,080-event backlog drained in about 21 s (roughly 1,900 events/s,
  10 s polling granularity).
* The ingest HTTP path was **not** saturated: the load generator (Python/httpx, same machine) is the limit
  at these rates, so the ceiling is unknown, not 3,163 events/s. Latency is client-side and includes that
  contention. Not a capacity claim for any real deployment; a hosted Postgres will behave differently.

## Limits and what is not done

Compared with real Sentry, and as a project:

* **Not deployed.** `render.yaml` (web, ingest, worker, Postgres, Redis, demo) has not been applied, so
  there are no live URLs and the free-tier behaviour is unknown. The GitHub Actions workflow has not
  run yet. Email alerts were tested with a fake SMTP server only; no real mail server was used.
* Auth is deliberately basic: email + password, no email verification, password reset, SSO or 2FA; the
  login throttle is in-process (per replica); sessions last 14 days and there is no session list. Two roles only.
* Grouping is a fixed stack-hash heuristic. You can merge issues by hand (the old fingerprint then aliases
  to the survivor), but there is no un-merge, no custom fingerprint rules and no similarity grouping.
* Source maps for JavaScript only, uploaded by file name + release (no `sourceMappingURL` discovery, no
  debug ids, no artifact bundles). No symbolication for Python or native code.
* Alerts: new-issue and per-issue spike only; webhook or email. No per-environment rules, no
  escalation, no Slack/PagerDuty integrations. A new rule can take up to 5 s to be noticed by a worker.
* No performance tracing, session replay, release health or user feedback. Only Python and JS SDKs; the
  JS SDK does not gzip.
* The optional AI summary was not built (it would need an API key and evaluation I could not run here).
* Throughput beyond what is measured above is unknown, and scaling to several workers/replicas was not
  measured (the code is built for it: consumer groups, advisory-locked migrations, idempotent inserts).
