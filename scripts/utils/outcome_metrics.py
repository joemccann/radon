"""REL-320 / R-027: bounded process counters for core operation outcomes.

Counters are observations, not an exactly-once execution ledger. Submission
success means the broker call returned, not that IB acknowledged or filled it.
Rates use process-monotonic age and reset on restart. Fixed labels contain no
SQL, trade identity, provider body or credentials. Emit cumulative samples to
journalctl at most once per minute; process exit flushes the final sample.
No storage, network, retry or trading decision depends on telemetry.
"""
from __future__ import annotations

import atexit
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import wraps
import json
import logging
import os
import sys
import threading
import time

OPERATIONS = ("order_submit", "order_modify", "fill_detected", "filled_order_removed", "journal_upsert",
              "database", "notification")
OUTCOMES = ("success", "error", "skipped")
log = logging.getLogger("radon.operation_metrics")


class OutcomeCounters:
    def __init__(self, *, clock=time.monotonic):
        self._clock = clock
        self._started = clock()
        self._started_at = datetime.now(timezone.utc).isoformat()
        self._last_emitted = self._started
        self._lock = threading.Lock()
        self._counts = {operation: dict.fromkeys(OUTCOMES, 0) for operation in OPERATIONS}
        self._dirty = set()

    def record(self, operation: str, outcome: str) -> None:
        with self._lock:
            self._counts[operation][outcome] += 1
            self._dirty.add(operation)
        self.emit()

    def emit(self, *, force: bool = False) -> None:
        now = self._clock()
        with self._lock:
            if not force and now - self._last_emitted < 60:
                return
            elapsed = max(0.0, now - self._started)
            samples = []
            for operation in sorted(self._dirty):
                counts = dict(self._counts[operation])
                attempts = counts["success"] + counts["error"]
                samples.append({
                    "pid": os.getpid(), "started_at": self._started_at,
                    "operation": operation, **counts,
                    "observed_seconds": elapsed,
                    "error_ratio": counts["error"] / attempts if attempts else None,
                    "success_per_second": counts["success"] / elapsed if elapsed else None,
                })
            self._dirty.clear()
            self._last_emitted = now
        for sample in samples:
            log.info("operation_metrics %s", json.dumps(sample, sort_keys=True))


METRICS = OutcomeCounters()


def record_outcome(operation: str, outcome: str) -> None:
    """Observation only. A counter or log failure must not change the caller.

    place_order reports failure before ib_place_order can mark the submit as
    transmitted, and the fill monitor records a fill before it writes the
    journal. Either path would treat a metrics fault as a missed broker action.
    """
    try:
        METRICS.record(operation, outcome)
    except Exception:
        try:
            log.warning("operation_metrics observation failed for %s", operation)
        except Exception:
            return


@contextmanager
def count_operation(operation: str):
    successful = False
    try:
        yield
        successful = True
    finally:
        record_outcome(operation, "success" if successful else "error")


def measure_operation(operation: str):
    def decorate(function):
        @wraps(function)
        def measured(*args, **kwargs):
            with count_operation(operation):
                return function(*args, **kwargs)
        return measured
    return decorate


# Resolve the current collector at exit (tests can inject their own clock).
atexit.register(lambda: METRICS.emit(force=True))

# Script entrypoints import utils.*, while root callers import scripts.utils.*.
# Both spellings must observe the same process counters (REL-320).
sys.modules.setdefault("utils.outcome_metrics", sys.modules[__name__])
sys.modules.setdefault("scripts.utils.outcome_metrics", sys.modules[__name__])
