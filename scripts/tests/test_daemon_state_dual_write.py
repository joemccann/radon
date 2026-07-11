"""Phase 4 — verify monitor_daemon save_state writes a daemon_state row
per handler. Mocks db.writer.upsert_daemon_state and asserts call shape.
"""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from typing import Any

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_SCRIPTS_DIR = _PROJECT_ROOT / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


@pytest.fixture
def mock_writer(monkeypatch: pytest.MonkeyPatch):
    calls: list[dict[str, Any]] = []
    fake = types.ModuleType("db.writer")
    fake.upsert_daemon_state = lambda handler, *, last_run=None, last_status=None, last_error=None: calls.append(  # type: ignore[attr-defined]
        {
            "handler": handler,
            "last_run": last_run,
            "last_status": last_status,
            "last_error": last_error,
        }
    )
    monkeypatch.setitem(sys.modules, "db.writer", fake)
    yield calls


class FakeHandler:
    def __init__(self, name: str, state: dict[str, Any] | None = None):
        self.name = name
        self._state = state or {}

    def get_state(self) -> dict[str, Any]:
        return self._state


class RemoteWriterPanic(BaseException):
    """Driver-level failure that intentionally bypasses ``Exception``."""


def test_save_state_writes_one_daemon_state_row_per_handler(
    mock_writer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    from monitor_daemon.daemon import MonitorDaemon

    daemon = MonitorDaemon(state_file=tmp_path / "daemon_state.json", respect_market_hours=False)
    daemon.handlers = [
        FakeHandler("fill_monitor", {"last_run": "2026-05-07T01:00:00Z", "last_status": "ok"}),
        FakeHandler("exit_orders", {"last_run": "2026-05-07T01:00:30Z", "last_status": "ok"}),
        FakeHandler("journal_sync", {"last_run": "2026-05-07T01:01:00Z", "last_status": "ok"}),
    ]

    daemon.save_state()

    handlers_written = sorted(c["handler"] for c in mock_writer)
    assert handlers_written == ["exit_orders", "fill_monitor", "journal_sync"]


def test_save_state_propagates_last_status_and_error_when_present(
    mock_writer, tmp_path: Path,
):
    from monitor_daemon.daemon import MonitorDaemon

    daemon = MonitorDaemon(state_file=tmp_path / "daemon_state.json", respect_market_hours=False)
    daemon.handlers = [
        FakeHandler("fill_monitor", {
            "last_run": "2026-05-07T01:00:00Z",
            "last_status": "error",
            "last_error": "IB connection refused",
        }),
    ]

    daemon.save_state()

    assert mock_writer[0]["last_status"] == "error"
    assert mock_writer[0]["last_error"] == "IB connection refused"


def test_save_state_falls_back_to_saved_at_when_handler_lacks_last_run(
    mock_writer, tmp_path: Path,
):
    from monitor_daemon.daemon import MonitorDaemon

    daemon = MonitorDaemon(state_file=tmp_path / "daemon_state.json", respect_market_hours=False)
    daemon.handlers = [FakeHandler("h_no_last_run", {})]
    daemon.save_state()

    # The dual-write should still happen; last_run defaults to the
    # saved_at timestamp from the JSON state.
    assert len(mock_writer) == 1
    assert mock_writer[0]["last_run"] is not None


def test_save_state_still_writes_json_when_db_dual_write_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    """Disk write is the failure-safe path; DB failure must not block it."""
    from monitor_daemon.daemon import MonitorDaemon

    fake = types.ModuleType("db.writer")
    fake.upsert_daemon_state = lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom"))  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "db.writer", fake)

    state_path = tmp_path / "daemon_state.json"
    daemon = MonitorDaemon(state_file=state_path, respect_market_hours=False)
    daemon.handlers = [FakeHandler("h", {"last_status": "ok"})]
    daemon.save_state()  # must not raise

    assert state_path.exists()
    payload = json.loads(state_path.read_text())
    assert "h" in payload["handlers"]


def test_save_state_contains_driver_panic_and_continues_remaining_rows(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    from monitor_daemon.daemon import MonitorDaemon

    attempts: list[str] = []

    def upsert(handler, **_kwargs):
        attempts.append(handler)
        if handler == "first":
            raise RemoteWriterPanic("could not load platform certs")

    fake = types.ModuleType("db.writer")
    fake.upsert_daemon_state = upsert  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "db.writer", fake)

    state_path = tmp_path / "daemon_state.json"
    daemon = MonitorDaemon(state_file=state_path, respect_market_hours=False)
    daemon.handlers = [
        FakeHandler("first", {"last_status": "ok"}),
        FakeHandler("second", {"last_status": "ok"}),
    ]

    with caplog.at_level("WARNING", logger="monitor_daemon.daemon"):
        daemon.save_state()

    assert attempts == ["first", "second"]
    payload = json.loads(state_path.read_text())
    assert set(payload["handlers"]) == {"first", "second"}
    assert "first" in caplog.text
    assert "could not load platform certs" in caplog.text


@pytest.mark.parametrize(
    "termination",
    [KeyboardInterrupt(), SystemExit(2), GeneratorExit()],
    ids=["keyboard_interrupt", "system_exit", "generator_exit"],
)
def test_save_state_does_not_swallow_operator_termination(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, termination: BaseException
):
    from monitor_daemon.daemon import MonitorDaemon

    fake = types.ModuleType("db.writer")

    def terminate(*_args, **_kwargs):
        raise termination

    fake.upsert_daemon_state = terminate  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "db.writer", fake)
    daemon = MonitorDaemon(
        state_file=tmp_path / "daemon_state.json",
        respect_market_hours=False,
    )
    daemon.handlers = [FakeHandler("h", {"last_status": "ok"})]

    with pytest.raises(type(termination)):
        daemon.save_state()


def test_save_state_does_nothing_when_state_file_unset(mock_writer):
    from monitor_daemon.daemon import MonitorDaemon

    daemon = MonitorDaemon(state_file=None, respect_market_hours=False)
    daemon.handlers = [FakeHandler("h", {"last_status": "ok"})]
    daemon.save_state()

    assert mock_writer == []
