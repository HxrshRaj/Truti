# Truti

*Truti* means "error" in Hindi. It is a small, honest version of what Sentry does: SDKs capture errors,
an ingest service validates and queues them, a worker groups them into issues, and a web app shows them.

**Status:** everything below runs locally via Docker Compose and passes its tests. It is **not deployed**:
`render.yaml` is written but has never been applied (see [Limits](#limits-and-what-is-not-done)).

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
| `ingest/` | FastAPI ingest API, Redis Streams worker, Postgres models, dashboard API (`/api/0`), source-map symbolication, alert webhooks, Prometheus metrics, load test |
| `web/` | React + Vite dashboard: projects/DSN, issues list (sparkline, filters, search), issue detail (stack frames with source context, breadcrumbs, tags, release, user), alert rules |
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

Tests (CI runs the same in `.github/workflows/ci.yml`; the workflow itself has not run on GitHub yet):

```bash
cd sdk-python && pip install -e ".[test]" && pytest     # 27 tests
cd ingest     && pip install -e ".[test]" && pytest     # 34 tests
cd sdk-js     && npm ci && npm test                     # 17 tests (vitest)
cd web        && npm ci && npm test && npm run build    # 3 tests (vitest)
```

All 81 passed locally on the merged `main`.

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
hand-built map. The demo ships a minified browser bundle with a real map and an upload script, but I did
not run that browser path end to end.

## Reliability and limits of the pipeline

* SDKs never raise into the host app, bound their queue, retry 5xx/429/network errors with exponential
  backoff and full jitter, honour `Retry-After`, and drop (not retry) other 4xx. `flush(timeout)` blocks
  until drained or timed out.
* Ingest: 401 bad key, 413 over `MAX_BODY_BYTES` (200 KB), 400 bad JSON, 422 schema errors with field
  paths, 429 + `Retry-After` per-project rate limit (fixed 1 s window, default 200 events/s), 503 +
  `Retry-After` when the stream is full, de-dup by event id (1 h window).
* Worker: at-least-once delivery. Failed messages are retried via `XAUTOCLAIM` and dead-lettered after
  3 deliveries.
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
| 1 event / envelope | 531 | 52 ms | 102 ms | 251 ms | 0 of 10,716 (0.00%) |
| 10 events / envelope | 2,408 | 68 ms | 137 ms | 174 ms | 41,490 of 90,170 (46.0%), all HTTP 503 |

What this says, honestly:

* The HTTP path is not the bottleneck; **the single worker is**. It stores about 90-100 events/s
  (roughly 10 ms per event, several round trips plus a commit each). With larger envelopes the stream
  filled to its 100,000 cap and ingest correctly answered `503` + `Retry-After`. The SDKs retry 503, so
  that is backpressure, not loss, but a client without retries would lose those events.
* **No loss among accepted events:** 59,396 returned 202; once the queue drained, Postgres held exactly
  59,396 distinct event ids, with 0 dead-lettered. Draining took about 6 minutes.
* Latency is client-side and includes the load generator competing for the same CPUs. Not a capacity
  claim for any real deployment. More workers would raise the drain rate; that was not measured.

## Limits and what is not done

Compared with real Sentry, and as a project:

* **Not deployed.** `render.yaml` (web, ingest, worker, Postgres, Redis, demo) has not been applied, so
  there are no live URLs and the free-tier behaviour is unknown. The GitHub Actions workflow has not
  run yet.
* No auth or multi-user model: one shared `ADMIN_TOKEN` guards the dashboard API; there are no
  organisations, users or per-project permissions.
* Tables are created on startup; no migrations. No data retention or event pruning.
* Grouping is a fixed stack-hash heuristic: no merging or splitting of issues, no custom fingerprint
  rules, no ML similarity.
* Source maps for JavaScript only (no Python/native symbolication, no source map discovery via
  `sourceMappingURL`, no artifact bundles, no minified-name fallback heuristics).
* Alerts are webhooks only (new issue, spike per issue); no email, no per-environment rules, no
  alert deduplication across rules.
* No performance tracing, session replay, release health, or user feedback. Only Python and JS SDKs.
* The optional AI summary was not built.
* The ingest accepts only JSON envelopes (no gzip, no attachments), and the worker is one process per
  container with per-event commits, which is the measured throughput limit above.
