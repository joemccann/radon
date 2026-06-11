"""The best-effort DB mirror must never block the FastAPI event loop.

Regression for the 2026-06-11 incident: `_maybe_dual_write_to_db` ran a
synchronous libsql write (`upsert_vcg_snapshot` + `record_service_health`)
inline on the single event loop. When a direct-cloud libsql write hung on a
Turso/network blip, the whole API froze (py-spy showed the MainThread blocked
in `upsert_vcg_snapshot`), so `/health` and every request timed out until a
restart. The fix hands the write to a bounded background daemon thread.
"""

import threading
import time

from scripts.api import server


def test_maybe_dual_write_does_not_block_the_caller(monkeypatch):
    started = threading.Event()
    release = threading.Event()

    def slow_write(path, data):
        started.set()
        # Simulate a hung libsql write.
        release.wait(timeout=5)

    monkeypatch.setattr(server, "_do_dual_write_to_db", slow_write)

    t0 = time.monotonic()
    server._maybe_dual_write_to_db(server.Path("vcg.json"), {"x": 1})
    elapsed = time.monotonic() - t0

    # The caller returns at once (it only enqueues), NOT after the blocking
    # write. A regression here would re-freeze the event loop.
    assert elapsed < 0.5, f"caller blocked {elapsed:.2f}s on the DB write"
    # The background worker actually picks up and runs the job.
    assert started.wait(timeout=3), "background db-mirror worker did not run"
    release.set()


def test_maybe_dual_write_survives_a_full_queue(monkeypatch):
    # Block the worker so the bounded queue fills; enqueuing past capacity must
    # never raise or block (best-effort mirror), just drop the overflow.
    block = threading.Event()

    def blocked_write(path, data):
        block.wait(timeout=5)

    monkeypatch.setattr(server, "_do_dual_write_to_db", blocked_write)
    try:
        for _ in range(server._DB_MIRROR_QUEUE.maxsize + 50):
            server._maybe_dual_write_to_db(server.Path("vcg.json"), {})
    finally:
        block.set()
