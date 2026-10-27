# Ingest load test: measured results

Environment: Windows 11 laptop, Docker Desktop (12 vCPUs, 7.6 GiB), everything on one machine:
Postgres 16, Redis 7, 1 ingest container (uvicorn, 2 workers), **1 worker container**, and the load
generator, all sharing the same CPUs. Rate limit raised to 1,000,000 events/s for the test
(otherwise the default 200 events/s/project limit applies). Not production hardware; do not read these as capacity claims.

Reproduce:

```bash
cd ingest/loadtest
RATE_LIMIT_EPS=1000000 docker compose -f compose.yml -p trutiload up --build -d
curl -s -XPOST localhost:8000/api/0/projects -H "Authorization: Bearer dev-admin-token" \
     -H 'content-type: application/json' -d '{"name":"load"}'      # note id + public_key
docker run --rm --network trutiload_default -v "$PWD:/lt" python:3.11-slim sh -c \
  "pip install -q httpx && python /lt/loadtest.py --url http://ingest:8000 --project <ID> --key <KEY> \
   --duration 20 --processes 8 --concurrency 4 --batch 1"
```

| run (20 s, 32 connections) | requests/s | events accepted/s | p50 | p95 | p99 | not accepted |
|---|---|---|---|---|---|---|
| 1 event per envelope | 531 | 531 | 52 ms | 102 ms | 251 ms | 0 of 10,716 (0.00%) |
| 10 events per envelope | 446 | 2,408 | 68 ms | 137 ms | 174 ms | 41,490 of 90,170 (46.0%), all HTTP 503 |

Latency is client-side, per request, including the load generator's own CPU contention.

What the numbers mean:

* **Zero loss for accepted events.** 59,396 events returned 202; after the queue drained,
  Postgres held exactly 59,396 distinct event ids for that project, 0 dead-lettered.
* **The worker is the bottleneck, not the HTTP path.** One worker process stores about 90-100
  events/s (about 10 ms/event: several Postgres round trips plus a commit per event). In the batch-10 run
  the stream hit `STREAM_MAXLEN` (100,000), so ingest returned `503` + `Retry-After: 5` for the rest.
  That is the designed backpressure: the Python SDK retries 503, so those events are delayed, not lost,
  but a client with no retry would lose them. It took about 6 minutes to drain the backlog.
* The first runs in this directory (`result-*.txt`) used one Python client process and measured the
  *client* (about 40-170 req/s) rather than the server; they are kept only to show that. `localhost`
  also stalled on IPv6 fallback on Windows, so use `127.0.0.1`.
* Not measured: multi-host deployment, Render's free tier, long soak runs.
