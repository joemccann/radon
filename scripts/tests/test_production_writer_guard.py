"""A developer host in cloud mode must never write production Turso rows.

2026-10-22: the laptop's com.radon.monitor-daemon launchd agent survived
``cloud.sh`` (``launchctl unload`` is not durable) and overwrote the VPS
writers' service_health rows for execution-sweep, fill-monitor and
menthorq-session every minute. Host config was the only enforcement; this
makes the single-writer rule self-enforcing in code.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utils import production_writer_guard as guard  # noqa: E402


class TestRefusalReason:
    def test_cloud_mode_on_macos_is_refused(self):
        reason = guard.refusal_reason({"RADON_MODE": "hetzner"}, platform="darwin")
        assert reason and "RADON_MODE=hetzner" in reason

    def test_cloud_mode_on_linux_is_the_vps_and_allowed(self):
        assert guard.refusal_reason({"RADON_MODE": "hetzner"}, platform="linux") is None

    def test_local_mode_on_macos_is_allowed(self):
        assert guard.refusal_reason({"RADON_MODE": "local"}, platform="darwin") is None

    def test_unset_mode_is_allowed(self, tmp_path: Path):
        assert guard.refusal_reason({}, platform="darwin", ib_mode_file=tmp_path / "none") is None

    def test_mode_falls_back_to_the_ib_mode_overlay(self, tmp_path: Path):
        overlay = tmp_path / ".env.ib-mode"
        overlay.write_text("IB_GATEWAY_HOST=ib-gateway\nRADON_MODE=hetzner\n", encoding="utf-8")
        assert guard.refusal_reason({}, platform="darwin", ib_mode_file=overlay)

    def test_refuse_raises(self):
        with pytest.raises(guard.ProductionWriterRefused):
            guard.refuse_developer_host_in_cloud_mode({"RADON_MODE": "hetzner"}, platform="darwin")


class TestHranaWriteChokepoint:
    def test_hrana_execute_refuses_before_any_request(self, monkeypatch: pytest.MonkeyPatch):
        from db import hrana_http

        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.setenv("RADON_MODE", "hetzner")
        monkeypatch.setenv("RADON_DB_TEST_WRITE_OK", "1")
        monkeypatch.setenv("TURSO_DB_URL", "libsql://synthetic.example.test")
        monkeypatch.setenv("TURSO_AUTH_TOKEN", "synthetic-token")
        posted = MagicMock(side_effect=AssertionError("must not reach the wire"))
        monkeypatch.setattr(hrana_http, "_post_pipeline", posted)
        with pytest.raises(guard.ProductionWriterRefused):
            hrana_http.hrana_execute("UPDATE service_health SET state = 'error'")
        posted.assert_not_called()

    def test_hrana_execute_still_writes_on_the_vps(self, monkeypatch: pytest.MonkeyPatch):
        from db import hrana_http

        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setenv("RADON_MODE", "hetzner")
        monkeypatch.setenv("RADON_DB_TEST_WRITE_OK", "1")
        monkeypatch.setenv("TURSO_DB_URL", "libsql://synthetic.example.test")
        monkeypatch.setenv("TURSO_AUTH_TOKEN", "synthetic-token")
        posted = MagicMock(return_value={"results": [{"type": "ok"}]})
        monkeypatch.setattr(hrana_http, "_post_pipeline", posted)
        monkeypatch.setattr(hrana_http, "_check_statement_ok", lambda body: None)
        hrana_http.hrana_execute("UPDATE service_health SET state = 'ok'")
        posted.assert_called_once()


class TestMonitorDaemonEntrypoint:
    def test_once_is_refused_on_a_developer_host_in_cloud_mode(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ):
        import monitor_daemon.run as run

        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.setenv("RADON_MODE", "hetzner")
        monkeypatch.setattr(sys, "argv", ["run.py", "--once"])
        monkeypatch.setattr(run, "create_daemon", MagicMock(side_effect=AssertionError("no daemon")))
        with pytest.raises(SystemExit) as caught:
            run.main()
        assert caught.value.code == guard.EXIT_REFUSED
        assert "RADON_MODE=hetzner" in capsys.readouterr().err

    def test_once_runs_in_local_mode(self, monkeypatch: pytest.MonkeyPatch):
        import monitor_daemon.run as run

        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.setenv("RADON_MODE", "local")
        monkeypatch.setattr(sys, "argv", ["run.py", "--once"])
        daemon = MagicMock()
        monkeypatch.setattr(run, "create_daemon", MagicMock(return_value=daemon))
        run_once = MagicMock(return_value={})
        monkeypatch.setattr(run, "run_once", run_once)
        run.main()
        run_once.assert_called_once_with(daemon)
