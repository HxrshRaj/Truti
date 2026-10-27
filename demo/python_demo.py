"""Deliberately broken Python app. Sends real errors to a Truti ingest service.

    TRUTI_DSN=http://<key>@localhost:8000/1 python demo/python_demo.py [rounds]

Distinct bugs -> distinct issues; repeating a bug -> same issue, higher count.
"""
import logging
import os
import sys
import time

import truti
from truti.integrations.logging import install as install_logging

dsn = os.environ.get("TRUTI_DSN")
if not dsn:
    sys.exit("set TRUTI_DSN (see 'Projects & DSN' in the dashboard)")

truti.init(dsn, release=os.environ.get("RELEASE", "demo-1.0.0"), environment="demo")
install_logging()
log = logging.getLogger("demo")
logging.basicConfig(level=logging.INFO)


def parse_age(raw):
    return int(raw)                      # ValueError for non-numbers


def lookup_user(users, uid):
    return users[uid]["email"]           # KeyError


def divide(a, b):
    return a / b                         # ZeroDivisionError


def run_round(i):
    truti.set_tag("round", str(i))
    truti.set_user({"id": f"user-{i % 3}"})
    truti.add_breadcrumb("starting round", category="demo")
    for fn, args in [(parse_age, ("forty-two",)), (lookup_user, ({}, 99)), (divide, (1, 0))]:
        try:
            fn(*args)
        except Exception:
            log.exception("round %s: %s failed", i, fn.__name__)   # logging integration -> event


if __name__ == "__main__":
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    for i in range(rounds):
        run_round(i)
        time.sleep(0.2)
    print("flushed:", truti.flush(10), "stats:", truti.get_client().transport.stats)
    raise RuntimeError("unhandled crash at end of demo")      # captured by sys.excepthook
