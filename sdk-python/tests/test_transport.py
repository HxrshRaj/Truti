import time

import truti
from truti.transport import Transport


def test_server_down_never_raises_and_flush_returns(init_sdk, server):
    server.stop()
    init_sdk(max_retries=1, timeout=0.3)
    t0 = time.time()
    truti.capture_message("x")          # must not raise or block
    assert time.time() - t0 < 0.2
    truti.flush(3)
    assert truti.get_client().transport.stats["dropped_failed"] == 1


def test_retry_with_backoff_then_success(init_sdk, server):
    server.script = [(500, {}, 0), (503, {}, 0)]
    init_sdk()
    truti.capture_message("x")
    assert truti.flush(5)
    assert len(server.events) == 1
    assert truti.get_client().transport.stats["retries"] == 2


def test_honours_retry_after_on_429(init_sdk, server):
    server.script = [(429, {"Retry-After": "1"}, 0)]
    init_sdk()
    t0 = time.time()
    truti.capture_message("x")
    assert truti.flush(5)
    assert time.time() - t0 >= 0.9
    assert len(server.events) == 1


def test_4xx_is_dropped_not_retried(init_sdk, server):
    server.script = [(400, {}, 0)]
    init_sdk()
    truti.capture_message("x")
    truti.flush(3)
    t = truti.get_client().transport
    assert t.stats["retries"] == 0 and t.stats["dropped_failed"] == 1


def test_slow_server_does_not_block_caller_and_flush_times_out(init_sdk, server):
    server.default = (202, {}, 1.5)
    init_sdk(timeout=3)
    t0 = time.time()
    truti.capture_message("x")
    assert time.time() - t0 < 0.2
    assert truti.flush(0.2) is False     # still in flight
    assert truti.flush(5) is True


def test_slow_server_with_short_timeout_gives_up(init_sdk, server):
    server.default = (202, {}, 1.0)
    init_sdk(timeout=0.2, max_retries=0)
    truti.capture_message("x")
    truti.flush(4)
    assert truti.get_client().transport.stats["dropped_failed"] == 1


def test_queue_full_drops_and_counts(server):
    server.default = (202, {}, 0.5)
    t = Transport(server.dsn, max_queue=3, batch_size=1, flush_interval=0.05)
    results = [t.enqueue({"event_id": str(i)}) for i in range(30)]
    assert False in results
    assert t.stats["dropped_queue_full"] == results.count(False)
    t.close(0.1)


def test_batching_groups_events(server):
    t = Transport(server.dsn, batch_size=10, flush_interval=0.3)
    for i in range(5):
        t.enqueue({"event_id": str(i)})
    assert t.flush(3)
    assert sum(len(env["events"]) for _, env in server.received) == 5
    assert len(server.received) < 5       # batched, not one request each
    t.close()


def test_sdk_internal_error_does_not_crash_app(init_sdk):
    init_sdk()
    c = truti.get_client()
    c.transport.enqueue = lambda e: 1 / 0
    assert truti.capture_message("x") is None
    assert truti.capture_exception(ValueError("v")) is None
    assert truti.flush(0.1) in (True, False)


def test_unserialisable_data_does_not_kill_worker(init_sdk, server):
    init_sdk()
    truti.add_breadcrumb("odd", data={"obj": object()})
    truti.capture_message("a")
    truti.capture_message("b")
    assert truti.flush(3)
    assert len(server.events) == 2
