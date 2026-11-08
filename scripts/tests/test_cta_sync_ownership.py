"""§3.4 CTA sync: only the lock holder writes the shared status, and the lock
is held by the kernel, not inferred from a pid in a file.

  * A lock-busy trigger wrote `syncing` over the shared status file. When the
    active run had already finished, `syncing` stuck until the next run.
  * `mkdir` then `lock.json` left a window in which a contender saw a lock
    directory with no metadata and rmtree'd a LIVE lock.
  * Liveness was `os.kill(pid, 0)` on a pid read from a file: pid reuse kept a
    dead lock, and a SIGTERM (systemd TimeoutStartSec) left `syncing`.
"""

from __future__ import annotations

import contextlib
import signal
import subprocess
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

import cta_sync_service as svc  # noqa: E402


@pytest.fixture
def lock_dir(tmp_path: Path) -> Path:
    return tmp_path / "locks" / "cta-sync.lock"


@pytest.fixture
def statuses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, lock_dir: Path) -> list[dict]:
    written: list[dict] = []
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    monkeypatch.setattr(svc, "LOCK_DIR", lock_dir)
    monkeypatch.setattr(svc, "ARTIFACT_DIR", tmp_path / "artifacts")
    monkeypatch.setattr(svc, "cache_path", lambda d: cache_dir / f"cta_{d}.json")
    monkeypatch.setattr(svc, "load_cta_sync_status", lambda: None)
    monkeypatch.setattr(svc, "latest_available_cta_date", lambda: "2026-09-16")
    monkeypatch.setattr(svc, "write_cta_sync_status", lambda p: written.append(dict(p)))
    monkeypatch.setattr(svc, "write_cta_sync_history", lambda p: None)
    monkeypatch.setattr(svc, "_dual_write_cta_to_db", lambda *a, **k: None)
    return written


def _lock(lock_dir: Path, run_id: str):
    return svc.cta_sync_lock(target_date="2026-09-17", run_id=run_id, lock_dir=lock_dir)


class TestOnlyTheHolderWritesStatus:
    def test_a_lock_busy_trigger_writes_no_status(self, statuses, lock_dir, monkeypatch):
        monkeypatch.setattr(
            svc.subprocess, "run", lambda *a, **k: pytest.fail("a lock-busy run fetched")
        )
        with _lock(lock_dir, "active"):
            assert svc.run_cta_sync(target_date="2026-09-17", source="test") == 0
        assert statuses == [], (
            "a duplicate trigger overwrote the shared status it does not own: "
            f"{statuses}"
        )


class TestTheLockIsHeldByTheKernel:
    def test_a_lock_dir_without_metadata_is_not_stolen(self, lock_dir):
        """The mkdir -> lock.json window: a contender must not rmtree a live lock."""
        with _lock(lock_dir, "active"):
            (lock_dir / "lock.json").unlink(missing_ok=True)
            with pytest.raises(svc.CtaSyncLockError):
                with _lock(lock_dir, "interloper"):
                    pytest.fail("acquired a lock another run holds")

    def test_a_reused_pid_in_stale_metadata_does_not_block(self, lock_dir):
        """Metadata naming a live pid that holds nothing is not a lock."""
        import json
        import os

        lock_dir.mkdir(parents=True)
        (lock_dir / "lock.json").write_text(json.dumps({"pid": os.getpid(), "run_id": "ghost"}))
        with _lock(lock_dir, "next"):
            pass

    def test_a_sigkilled_holder_releases_the_lock(self, lock_dir):
        child = subprocess.Popen(
            [sys.executable, "-c", textwrap.dedent(f"""
                import sys, time
                sys.path.insert(0, {str(SCRIPTS)!r})
                from pathlib import Path
                import cta_sync_service as svc
                with svc.cta_sync_lock(target_date="d", run_id="child", lock_dir=Path({str(lock_dir)!r})):
                    print("held", flush=True)
                    time.sleep(60)
            """)],
            stdout=subprocess.PIPE,
            text=True,
        )
        try:
            assert child.stdout.readline().strip() == "held"
            with pytest.raises(svc.CtaSyncLockError):
                with _lock(lock_dir, "contender"):
                    pass
            child.send_signal(signal.SIGKILL)
            child.wait(timeout=10)
            with _lock(lock_dir, "after-kill"):
                pass
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=10)


class TestAnInterruptedRunEndsTerminal:
    def test_sigterm_is_handled_during_the_run(self, statuses, monkeypatch):
        seen: list = []

        def fetch(*_a, **_k):
            seen.append(signal.getsignal(signal.SIGTERM))
            raise SystemExit(143)

        monkeypatch.setattr(svc.subprocess, "run", fetch)
        with pytest.raises(SystemExit):
            svc.run_cta_sync(target_date="2026-09-17", source="test")
        assert seen and seen[0] not in (signal.SIG_DFL, signal.SIG_IGN, None), (
            "SIGTERM (systemd TimeoutStartSec) kills the run without a final status"
        )
        assert signal.getsignal(signal.SIGTERM) is not seen[0], "handler not restored"

    def test_an_interrupted_run_does_not_leave_syncing(self, statuses, monkeypatch):
        def fetch(*_a, **_k):
            raise SystemExit(143)

        monkeypatch.setattr(svc.subprocess, "run", fetch)
        with pytest.raises(SystemExit):
            svc.run_cta_sync(target_date="2026-09-17", source="test")
        assert statuses, "no status written"
        assert statuses[-1]["state"] == "degraded"
        assert statuses[-1]["last_error"]["type"] == "interrupted"
        assert statuses[-1]["last_attempt_finished_at"]
