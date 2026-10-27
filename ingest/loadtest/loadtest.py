"""Load test for the ingest endpoint.

    python loadtest/loadtest.py --url http://localhost:8000 --project 1 --key <public_key> \
        --duration 20 --processes 6 --concurrency 8 --batch 1

Reports: requests/s, events/s, latency p50/p95/p99 (client-side, per request), HTTP status
counts, and drop rate = events not accepted (anything but 202, plus transport errors) / events sent.
With --metrics-url it also scrapes /metrics before/after so server counters can be compared.
Needs only httpx.
"""
import argparse
import asyncio
import datetime as dt
import multiprocessing
import statistics
import time
import uuid

import httpx


def make_event(i):
    return {
        "event_id": uuid.uuid4().hex,
        "timestamp": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "platform": "python", "level": "error", "release": "load-1", "environment": "loadtest",
        "exception": {"type": f"LoadError{i % 10}", "value": f"synthetic failure {i}", "frames": [
            {"filename": "app/handlers.py", "function": f"handler_{i % 10}", "lineno": 42,
             "context_line": "do_thing(x)", "pre_context": ["a = 1", "b = 2"], "post_context": ["return a"],
             "in_app": True},
            {"filename": "app/db.py", "function": "query", "lineno": 7, "context_line": "cur.execute(q)",
             "in_app": True}]},
        "breadcrumbs": [{"timestamp": "2026-01-01T00:00:00Z", "category": "log", "message": "step", "level": "info"}] * 5,
        "tags": {"region": "ap-south-1"}, "sdk": {"name": "loadtest", "version": "1"},
    }


async def worker(client, url, key, batch, deadline, lat, status, counters):
    n = 0
    while time.perf_counter() < deadline:
        events = [make_event(n + k) for k in range(batch)]
        n += batch
        body = {"envelope_version": 1, "events": events}
        t0 = time.perf_counter()
        try:
            r = await client.post(url, json=body, headers={"X-Truti-Auth": f"truti_key={key}"})
            code = r.status_code
        except Exception as e:
            code = type(e).__name__
        lat.append(time.perf_counter() - t0)
        status[code] = status.get(code, 0) + 1
        counters["sent"] += batch
        if code == 202:
            counters["accepted"] += batch


def pct(sorted_vals, p):
    return sorted_vals[min(len(sorted_vals) - 1, int(len(sorted_vals) * p))]


async def run_one(url, key, batch, concurrency, duration):
    lat, status, counters = [], {}, {"sent": 0, "accepted": 0}
    limits = httpx.Limits(max_connections=concurrency, max_keepalive_connections=concurrency)
    async with httpx.AsyncClient(limits=limits, timeout=10) as client:
        deadline = time.perf_counter() + duration
        await asyncio.gather(*(worker(client, url, key, batch, deadline, lat, status, counters)
                               for _ in range(concurrency)))
    return lat, status, counters


def proc_main(args):
    return asyncio.run(run_one(*args))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8000", help="use 127.0.0.1, not localhost (IPv6 fallback stalls on Windows)")
    ap.add_argument("--project", type=int, required=True)
    ap.add_argument("--key", required=True)
    ap.add_argument("--duration", type=float, default=20)
    ap.add_argument("--concurrency", type=int, default=8, help="connections per process")
    ap.add_argument("--processes", type=int, default=1, help="client processes (a single Python client saturates ~170 req/s)")
    ap.add_argument("--batch", type=int, default=1, help="events per envelope")
    a = ap.parse_args()
    url = f"{a.url.rstrip('/')}/api/{a.project}/envelope/"
    t0 = time.perf_counter()
    with multiprocessing.Pool(a.processes) as pool:
        results = pool.map(proc_main, [(url, a.key, a.batch, a.concurrency, a.duration)] * a.processes)
    elapsed = time.perf_counter() - t0
    lat = sorted(x for r in results for x in r[0])
    status = {}
    for r in results:
        for k, v in r[1].items():
            status[k] = status.get(k, 0) + v
    sent = sum(r[2]["sent"] for r in results)
    acc = sum(r[2]["accepted"] for r in results)
    print(f"duration_s        {elapsed:.1f}")
    print(f"connections       {a.processes} proc x {a.concurrency} = {a.processes * a.concurrency}  batch {a.batch}")
    print(f"requests          {len(lat)}  ({len(lat) / elapsed:.0f} req/s)")
    print(f"events accepted   {acc}  ({acc / elapsed:.0f} events/s)")
    print(f"latency ms        p50 {pct(lat, .5) * 1000:.1f}  p95 {pct(lat, .95) * 1000:.1f}  "
          f"p99 {pct(lat, .99) * 1000:.1f}  mean {statistics.mean(lat) * 1000:.1f}")
    print(f"status counts     {status}")
    print(f"drop rate         {(sent - acc) / sent * 100:.2f}%  ({sent - acc} of {sent} events not accepted)")


if __name__ == "__main__":
    main()
