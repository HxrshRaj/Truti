# Truti envelope format, version 1

SDKs send batches to `POST /api/{project_id}/envelope/`.

Headers:

- `Content-Type: application/json`
- `X-Truti-Auth: truti_key=<public key>` (`?truti_key=` also accepted, for `sendBeacon`)

Body (the "envelope"):

```json
{
  "envelope_version": 1,
  "sent_at": "2026-01-01T00:00:00.000Z",
  "events": [ <event>, ... ]          // 1..50 events
}
```

Event:

```json
{
  "event_id": "32 lowercase hex chars",
  "timestamp": "ISO-8601 UTC",
  "platform": "python | javascript",
  "level": "fatal | error | warning | info | debug",
  "message": "optional string",
  "release": "optional string",
  "environment": "optional string",
  "exception": {
    "type": "ValueError",
    "value": "bad input",
    "frames": [                       // oldest call first, newest last
      {
        "filename": "app/main.py",
        "function": "handler",
        "lineno": 12,
        "colno": 5,                   // JS only
        "context_line": "    x = int(s)",
        "pre_context": ["..."],
        "post_context": ["..."],
        "in_app": true
      }
    ]
  },
  "breadcrumbs": [{"timestamp": "...", "category": "log", "message": "...", "level": "info"}],
  "tags": {"k": "v"},
  "user": {"id": "1", "email": "a@b.c"},
  "request": {"method": "GET", "url": "/x"},
  "sdk": {"name": "truti.python", "version": "0.1.0"}
}
```

Responses: `202 {"accepted": n, "duplicates": n}`, `400/401/413/422` with
`{"error": "...", "details": [...]}`, `429` + `Retry-After` (seconds),
`503` + `Retry-After` when the queue is full.

An event needs either `exception` or `message`.
