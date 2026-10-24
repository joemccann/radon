"""A non-Linux host must never write production Turso rows (REL-330 / R-741).

2026-10-22: the laptop's com.radon.monitor-daemon launchd agent survived
``cloud.sh`` and overwrote fill-monitor, execution-sweep and
menthorq-session every minute. Its plist sets no ``RADON_MODE``. A guard
that treated an unset mode as permission let that agent through once
``.env.ib-mode`` stopped being loaded.
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

    def test_local_mode_on_macos_is_refused(self):
        assert guard.refusal_reason({"RADON_MODE": "local"}, platform="darwin")

    def test_unset_mode_on_macos_is_refused(self):
        assert guard.refusal_reason({}, platform="darwin")

    def test_mode_comes_from_the_environment_only(self):
        assert guard.radon_mode({"RADON_MODE": " 'Hetzner' "}) == "hetzner"
        assert guard.radon_mode({}) == ""

    def test_refuse_raises(self):
        with pytest.raises(guard.ProductionWriterRefused):
            guard.refuse_developer_host_in_cloud_mode({"RADON_MODE": "hetzner"}, platform="darwin")


class TestStrayLaunchdAgent:
    """The 2026-10-22 agent did not carry RADON_MODE.

    Its plist sets PATH and PYTHONPATH only. cloud.sh used to persist
    hetzner in .env.ib-mode, and this tree no longer loads that file, so
    the process that overwrote fill-monitor / execution-sweep /
    menthorq-session now arrives with the mode unset. Unset is not a
    permit: a non-Linux host has no production writer.
    """

    def test_unset_mode_on_macos_is_refused(self):
        reason = guard.refusal_reason({}, platform="darwin")
        assert reason
        assert "darwin" in reason

    def test_local_mode_on_macos_is_refused(self):
        reason = guard.refusal_reason({"RADON_MODE": "local"}, platform="darwin")
        assert reason

    def test_hrana_execute_refuses_an_unset_mode_before_any_request(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        from db import hrana_http

        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.delenv("RADON_MODE", raising=False)
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
        monkeypatch.setenv("TURSO_DB_URL", "libsql://synthetic.example.test")
        monkeypatch.setenv("TURSO_AUTH_TOKEN", "synthetic-token")
        posted = MagicMock(side_effect=AssertionError("must not reach the wire"))
        monkeypatch.setattr(hrana_http, "_post_pipeline", posted)
        with pytest.raises(guard.ProductionWriterRefused):
            hrana_http.hrana_execute("UPDATE service_health SET state = 'error'")
        posted.assert_not_called()

    def test_daemon_once_with_unset_mode_exits_before_handlers(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ):
        import monitor_daemon.run as run

        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.delenv("RADON_MODE", raising=False)
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
        monkeypatch.setattr(sys, "argv", ["run.py", "--once"])
        monkeypatch.setattr(run, "create_daemon", MagicMock(side_effect=AssertionError("no daemon")))
        with pytest.raises(SystemExit) as caught:
            run.main()
        assert caught.value.code == guard.EXIT_REFUSED
        assert "darwin" in capsys.readouterr().err

    def test_native_connection_refuses_before_connect(self, monkeypatch: pytest.MonkeyPatch):
        from db import client as db_client

        db_client.reset_for_tests()
        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
        monkeypatch.delenv("RADON_MODE", raising=False)
        connect = MagicMock(side_effect=AssertionError("must not connect"))
        monkeypatch.setattr(db_client.libsql, "connect", connect)
        with pytest.raises(guard.ProductionWriterRefused):
            db_client.get_db()
        connect.assert_not_called()
        db_client.reset_for_tests()

    def test_api_execute_and_transaction_refuse_before_any_request(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        from api import db_http

        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
        monkeypatch.delenv("RADON_MODE", raising=False)
        monkeypatch.setenv("TURSO_DB_URL", "libsql://synthetic.example.test")
        monkeypatch.setenv("TURSO_AUTH_TOKEN", "synthetic-token")
        posted = MagicMock(side_effect=AssertionError("must not reach the wire"))
        monkeypatch.setattr(db_http, "_post_pipeline", posted)
        monkeypatch.setattr(db_http, "_open_request", posted)
        with pytest.raises(guard.ProductionWriterRefused):
            db_http.hrana_execute("UPDATE service_health SET state = 'error'")
        with pytest.raises(guard.ProductionWriterRefused):
            db_http.hrana_transaction([("UPDATE service_health SET state = 'error'", ())])
        posted.assert_not_called()


class TestHranaWriteChokepoint:
    def test_hrana_execute_refuses_before_any_request(self, monkeypatch: pytest.MonkeyPatch):
        from db import hrana_http

        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.setenv("RADON_MODE", "hetzner")
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
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
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
        monkeypatch.setattr(sys, "argv", ["run.py", "--once"])
        monkeypatch.setattr(run, "create_daemon", MagicMock(side_effect=AssertionError("no daemon")))
        with pytest.raises(SystemExit) as caught:
            run.main()
        assert caught.value.code == guard.EXIT_REFUSED
        assert "RADON_MODE=hetzner" in capsys.readouterr().err

    def test_once_is_refused_in_local_mode_on_macos(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        import monitor_daemon.run as run

        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.setenv("RADON_MODE", "local")
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
        monkeypatch.setattr(sys, "argv", ["run.py", "--once"])
        monkeypatch.setattr(run, "create_daemon", MagicMock(side_effect=AssertionError("no daemon")))
        with pytest.raises(SystemExit) as caught:
            run.main()
        assert caught.value.code == guard.EXIT_REFUSED

    def test_once_runs_on_linux(self, monkeypatch: pytest.MonkeyPatch):
        import monitor_daemon.run as run

        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.delenv("RADON_MODE", raising=False)
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
        monkeypatch.setattr(sys, "argv", ["run.py", "--once"])
        daemon = MagicMock()
        monkeypatch.setattr(run, "create_daemon", MagicMock(return_value=daemon))
        run_once = MagicMock(return_value={})
        monkeypatch.setattr(run, "run_once", run_once)
        run.main()
        run_once.assert_called_once_with(daemon)
