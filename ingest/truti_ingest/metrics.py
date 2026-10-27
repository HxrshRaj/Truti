from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

REGISTRY = CollectorRegistry()
EVENTS_RECEIVED = Counter("truti_events_received_total", "Events in accepted envelopes", registry=REGISTRY)
EVENTS_REJECTED = Counter("truti_events_rejected_total", "Events/envelopes rejected", ["reason"], registry=REGISTRY)
EVENTS_QUEUED = Counter("truti_events_queued_total", "Events pushed to the stream", registry=REGISTRY)
EVENTS_DUPLICATE = Counter("truti_events_duplicate_total", "Events dropped as duplicate event_id", registry=REGISTRY)
REQUEST_LATENCY = Histogram("truti_ingest_request_seconds", "Envelope endpoint latency", registry=REGISTRY,
                            buckets=(.001, .0025, .005, .01, .025, .05, .1, .25, .5, 1))
EVENTS_PROCESSED = Counter("truti_events_processed_total", "Events stored by the worker", registry=REGISTRY)
EVENTS_FAILED = Counter("truti_events_failed_total", "Events the worker dead-lettered", registry=REGISTRY)
PROCESSING_LATENCY = Histogram("truti_processing_seconds", "Worker time to store one event", registry=REGISTRY,
                               buckets=(.001, .0025, .005, .01, .025, .05, .1, .25, .5, 1, 2.5))
QUEUE_LAG = Histogram("truti_queue_wait_seconds", "Time from ingest receipt to worker pickup", registry=REGISTRY,
                      buckets=(.01, .05, .1, .5, 1, 5, 30, 120))
QUEUE_DEPTH = Gauge("truti_queue_length", "Stream length", registry=REGISTRY)
