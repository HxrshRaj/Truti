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

**Current worker (one transaction per batch of up to 200 messages), 1 worker container:**

| run (20 s, 32 connections) | requests/s | events accepted/s | p50 | p95 | p99 | not accepted |
|---|---|---|---|---|---|---|
| 1 event per envelope | 623 | 623 | 47 ms | 86 ms | 128 ms | 0 of 12,557 (0.00%) |
| 10 events per envelope | 316 | 3,163 | 90 ms | 175 ms | 234 ms | 0 of 64,130 (0.00%) |

All 76,687 accepted events were stored (76,687 distinct event ids in Postgres), 0 dead-lettered, no 503s.
A 40,080-event backlog left over when the load stopped drained in about 21 s (about 1,900 events/s; polled
every 10 s, so +/- 10 s). Raw output: `RESULTS.txt`.

**Earlier worker (commit per event), same rig, kept for the comparison:** batch 1: 531 events/s, p95 102 ms,
0 of 10,716 dropped. Batch 10: 2,408 events/s accepted but 46.0% (41,490 of 90,170) got HTTP 503 because one
worker stored only about 90-100 events/s and the stream hit `STREAM_MAXLEN`. 59,396 accepted events were
stored as exactly 59,396 distinct rows; draining took about 6 minutes.

What the numbers mean:

* The HTTP path was not saturated: the Python/httpx load generator on the same machine is the limit, so the
  ceiling is unknown. Latency is client-side and includes that contention. Do not read these as capacity claims.
* Earlier runs with a single client process measured the *client* (about 40-170 req/s), not the server, and
  are discarded. `localhost` also stalled on IPv6 fallback on Windows, so use `127.0.0.1`.
* Not measured: multiple workers, multi-host deployment, Render's free tier, long soak runs.
