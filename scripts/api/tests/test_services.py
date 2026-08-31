"""Tests for ``scripts.api.services`` — the operator service-control module.

These pin down the security-relevant pieces:
  - Unit-name allowlist rejects anything outside ``radon-*``.
  - Action allowlist rejects anything outside start/stop/restart.
  - Non-systemd hosts degrade to ``supported=False`` instead of erroring.
  - ``control_unit`` shape-checks before invoking systemctl.
"""

from __future__ import annotations

import asyncio
import fcntl
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest


SCRIPTS_DIR = Path(__file__).resolve().parents[2]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


from scripts.api import services as admin_services  # noqa: E402


class TestIsValidUnit:
    @pytest.mark.parametrize(
        "unit",
        [
            "radon-api.service",
            "radon-relay.service",
            "radon-monitor.service",
            "radon-newsfeed.service",
            "radon-nextjs.service",
            "radon-ib-gateway.service",
            "radon-refresh.timer",
            "radon-vcg-refresh.timer",
            "radon-watchdog-intraday.service",
        ],
    )
    def test_accepts_radon_units(self, unit: str) -> None:
        assert admin_services.is_valid_unit(unit) is True

    @pytest.mark.parametrize(
        "unit",
        [
            "ssh.service",
            "nginx.service",
            "radon-../api.service",
            "radon-api.service && rm -rf /",
            "RADON-api.service",  # uppercase rejected
            ".",
            "",
            "../radon-api.service",
            "radon-ib-gateway-preheld-restart.service",
        ],
    )
    def test_rejects_non_radon_units(self, unit: str) -> None:
        assert admin_services.is_valid_unit(unit) is False


class TestControlUnitRejection:
    """The allowlist must fire BEFORE we touch systemctl."""

    def test_rejects_invalid_action(self) -> None:
        result = asyncio.run(
            admin_services.control_unit("radon-api.service", "enable"),
        )
        assert result.ok is False
        assert result.returncode == -1
        assert "action" in result.detail.lower()

    def test_rejects_invalid_unit(self) -> None:
        result = asyncio.run(
            admin_services.control_unit("nginx.service", "restart"),
        )
        assert result.ok is False
        assert result.returncode == -1
        assert "unit" in result.detail.lower()

    def test_rejects_injection_attempt(self) -> None:
        result = asyncio.run(
            admin_services.control_unit("radon-api.service && touch /tmp/hack", "restart"),
        )
        assert result.ok is False
        assert result.returncode == -1


class TestNonSystemdHost:
    """On macOS / laptop dev, systemctl is absent — degrade gracefully."""

    def test_list_units_returns_placeholder_catalogue(self) -> None:
        with patch.object(admin_services, "is_systemd_available", return_value=False):
            units = asyncio.run(admin_services.list_units())
        assert "radon-api.service" in units
        assert "radon-ib-gateway.service" in units

    def test_show_unit_reports_unsupported(self) -> None:
        with patch.object(admin_services, "is_systemd_available", return_value=False):
            status = asyncio.run(admin_services.show_unit("radon-api.service"))
        assert status.load_state == "unsupported"
        assert status.can_control is False

    def test_app_gateway_status_does_not_need_systemctl(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("RADON_HOST_ROLE", "app")
        ca = tmp_path / "ca.pem"
        cert = tmp_path / "client.pem"
        key = tmp_path / "client.key"
        for path in (ca, cert, key):
            path.write_text("x")
        monkeypatch.setenv("RADON_IB_REMOTE_URL", "https://10.0.0.4:8340")
        monkeypatch.setenv("RADON_IB_REMOTE_CA", str(ca))
        monkeypatch.setenv("RADON_IB_REMOTE_CLIENT_CERT", str(cert))
        monkeypatch.setenv("RADON_IB_REMOTE_CLIENT_KEY", str(key))
        remote = admin_services.ActionResult(
            "radon-ib-gateway.service", "status", True, "running", 0,
        )
        async def fake_remote(_action: str):
            return remote
        with patch.object(admin_services, "is_systemd_available", return_value=False), \
             patch.object(admin_services, "remote_gateway_action", side_effect=fake_remote):
            status = asyncio.run(admin_services.show_unit("radon-ib-gateway.service"))
        assert status.can_control is True
        assert status.active_state == "active"
        assert status.sub_state == "running"

    def test_control_unit_refuses_without_systemctl(self) -> None:
        with patch.object(admin_services, "is_systemd_available", return_value=False):
            result = asyncio.run(
                admin_services.control_unit("radon-api.service", "restart"),
            )
        assert result.ok is False
        assert "systemctl" in result.detail.lower()

    def test_list_units_with_status_returns_full_payload(self) -> None:
        with patch.object(admin_services, "is_systemd_available", return_value=False):
            statuses = asyncio.run(admin_services.list_units_with_status())
        assert len(statuses) > 0
        for s in statuses:
            assert s.load_state in {"unsupported", "rejected"}
            assert s.can_control is False


class TestParseShowOutput:
    def test_parses_systemctl_show_payload(self) -> None:
        raw = "LoadState=loaded\nActiveState=active\nSubState=running\nDescription=Radon API\n"
        parsed = admin_services._parse_show_output(raw)
        assert parsed == {
            "LoadState": "loaded",
            "ActiveState": "active",
            "SubState": "running",
            "Description": "Radon API",
        }

    def test_ignores_blank_lines(self) -> None:
        parsed = admin_services._parse_show_output("\nLoadState=loaded\n\n")
        assert parsed == {"LoadState": "loaded"}


class TestParseSystemctlTimestamp:
    """Cover the LC_ALL=C systemctl timestamp formats and never-set sentinels."""

    def test_parses_utc_timestamp(self) -> None:
        # Matches what `systemctl show -p ActiveEnterTimestamp` emits under LC_ALL=C.
        iso = admin_services.parse_systemctl_timestamp("Tue 2026-05-19 18:41:51 UTC")
        assert iso == "2026-05-19T18:41:51Z"

    def test_parses_without_timezone(self) -> None:
        # Some unit show outputs omit the trailing TZ when in monotonic-only mode.
        iso = admin_services.parse_systemctl_timestamp("Tue 2026-05-19 18:41:51")
        assert iso == "2026-05-19T18:41:51Z"

    def test_returns_none_for_unset_sentinels(self) -> None:
        assert admin_services.parse_systemctl_timestamp("") is None
        assert admin_services.parse_systemctl_timestamp("0") is None
        assert admin_services.parse_systemctl_timestamp("n/a") is None

    def test_returns_none_for_garbage(self) -> None:
        assert admin_services.parse_systemctl_timestamp("not a date") is None


class TestUnitStatusEnrichment:
    """Show that ``show_unit`` populates the new timestamp + uptime fields.

    Mocks the systemctl shell-out so we cover both simple-daemon and
    oneshot shapes without touching the OS.
    """

    def _make_show_output(self, **overrides: str) -> str:
        defaults = {
            "LoadState": "loaded",
            "ActiveState": "active",
            "SubState": "running",
            "Description": "Radon FastAPI",
            "Type": "simple",
            "ActiveEnterTimestamp": "Tue 2026-05-19 09:00:00 UTC",
            "InactiveEnterTimestamp": "",
            "ExecMainStartTimestamp": "Tue 2026-05-19 09:00:00 UTC",
            "ExecMainExitTimestamp": "",
            "ExecMainStatus": "",
        }
        defaults.update(overrides)
        return "\n".join(f"{k}={v}" for k, v in defaults.items())

    def test_simple_daemon_reports_uptime(self) -> None:
        """A running ``Type=simple`` unit gets uptime_secs from ActiveEnterTimestamp."""
        async def fake_systemctl(*_args: str, **_kwargs: object) -> tuple[str, str, int]:
            return (self._make_show_output(), "", 0)

        with patch.object(admin_services, "is_systemd_available", return_value=True):
            with patch.object(admin_services, "_systemctl", side_effect=fake_systemctl):
                status = asyncio.run(admin_services.show_unit("radon-api.service"))

        assert status.active_state == "active"
        assert status.sub_state == "running"
        assert status.last_active_at is not None
        assert status.last_active_at.startswith("2026-05-19T09:00:00")
        assert status.uptime_secs is not None and status.uptime_secs >= 0
        # Simple-daemon exit code is intentionally not surfaced.
        assert status.last_exit_code is None

    def test_oneshot_reports_last_exit_code_and_finish_time(self) -> None:
        """A completed ``Type=oneshot`` unit surfaces rc + last-finish time."""
        output = self._make_show_output(
            ActiveState="inactive",
            SubState="dead",
            Type="oneshot",
            ActiveEnterTimestamp="Tue 2026-05-19 11:55:00 UTC",
            InactiveEnterTimestamp="Tue 2026-05-19 11:55:30 UTC",
            ExecMainStartTimestamp="Tue 2026-05-19 11:55:00 UTC",
            ExecMainExitTimestamp="Tue 2026-05-19 11:55:30 UTC",
            ExecMainStatus="0",
        )

        async def fake_systemctl(*_args: str, **_kwargs: object) -> tuple[str, str, int]:
            return (output, "", 0)

        with patch.object(admin_services, "is_systemd_available", return_value=True):
            with patch.object(admin_services, "_systemctl", side_effect=fake_systemctl):
                status = asyncio.run(admin_services.show_unit("radon-cta-sync.service"))

        assert status.active_state == "inactive"
        assert status.last_exit_code == 0
        assert status.last_active_at == "2026-05-19T11:55:30Z"
        # Inactive oneshot doesn't have uptime semantics.
        assert status.uptime_secs is None

    def test_never_run_oneshot(self) -> None:
        """A unit that has never been started reports None across the board."""
        output = self._make_show_output(
            ActiveState="inactive",
            SubState="dead",
            Type="oneshot",
            ActiveEnterTimestamp="",
            InactiveEnterTimestamp="",
            ExecMainStartTimestamp="",
            ExecMainExitTimestamp="",
            ExecMainStatus="",
        )

        async def fake_systemctl(*_args: str, **_kwargs: object) -> tuple[str, str, int]:
            return (output, "", 0)

        with patch.object(admin_services, "is_systemd_available", return_value=True):
            with patch.object(admin_services, "_systemctl", side_effect=fake_systemctl):
                status = asyncio.run(admin_services.show_unit("radon-portfolio-sync.service"))

        assert status.last_active_at is None
        assert status.last_exit_code is None
        assert status.uptime_secs is None

    def test_failed_oneshot_propagates_nonzero_exit(self) -> None:
        output = self._make_show_output(
            ActiveState="failed",
            SubState="failed",
            Type="oneshot",
            ActiveEnterTimestamp="Tue 2026-05-19 11:55:00 UTC",
            InactiveEnterTimestamp="Tue 2026-05-19 11:55:05 UTC",
            ExecMainExitTimestamp="Tue 2026-05-19 11:55:05 UTC",
            ExecMainStatus="2",
        )

        async def fake_systemctl(*_args: str, **_kwargs: object) -> tuple[str, str, int]:
            return (output, "", 0)

        with patch.object(admin_services, "is_systemd_available", return_value=True):
            with patch.object(admin_services, "_systemctl", side_effect=fake_systemctl):
                status = asyncio.run(admin_services.show_unit("radon-cta-sync.service"))

        assert status.last_exit_code == 2
        assert status.active_state == "failed"

    def test_gateway_status_uses_real_container_not_active_oneshot(self) -> None:
        output = self._make_show_output(
            ActiveState="active",
            SubState="exited",
            Type="oneshot",
            ExecMainStatus="0",
        )

        async def fake_systemctl(*_args: str, **_kwargs: object) -> tuple[str, str, int]:
            return (output, "", 0)

        async def fake_gateway_helper(
            _action: str, *, timeout: float
        ) -> tuple[str, str, int]:
            assert timeout == 15.0
            return ("stopped", "", 1)

        with patch.object(admin_services, "is_systemd_available", return_value=True), \
             patch.object(admin_services, "is_gateway_control_available", return_value=True), \
             patch.object(admin_services, "_systemctl", side_effect=fake_systemctl), \
             patch.object(admin_services, "_run_gateway_helper", side_effect=fake_gateway_helper):
            status = asyncio.run(admin_services.show_unit("radon-ib-gateway.service"))

        assert status.active_state == "inactive"
        assert status.sub_state == "dead"
        assert status.can_control is True


class TestDeriveHelpers:
    """Lower-level helpers used inside show_unit; pin behaviour directly."""

    def test_derive_last_active_prefers_exec_main_exit(self) -> None:
        parsed = {
            "ActiveEnterTimestamp": "Tue 2026-05-19 09:00:00 UTC",
            "InactiveEnterTimestamp": "Tue 2026-05-19 09:30:00 UTC",
            "ExecMainExitTimestamp": "Tue 2026-05-19 10:00:00 UTC",
        }
        assert admin_services._derive_last_active(parsed) == "2026-05-19T10:00:00Z"

    def test_derive_last_active_returns_none_for_empty_inputs(self) -> None:
        assert admin_services._derive_last_active({}) is None

    def test_derive_uptime_returns_none_for_non_running_units(self) -> None:
        parsed = {
            "ActiveState": "inactive",
            "SubState": "dead",
            "ActiveEnterTimestamp": "Tue 2026-05-19 09:00:00 UTC",
        }
        assert admin_services._derive_uptime_secs(parsed) is None

    def test_derive_last_exit_code_skips_non_oneshot(self) -> None:
        parsed = {"Type": "simple", "ExecMainStatus": "0"}
        assert admin_services._derive_last_exit_code(parsed) is None

    def test_derive_last_exit_code_parses_oneshot_status(self) -> None:
        parsed = {"Type": "oneshot", "ExecMainStatus": "0"}
        assert admin_services._derive_last_exit_code(parsed) == 0
        parsed["ExecMainStatus"] = "2"
        assert admin_services._derive_last_exit_code(parsed) == 2

    def test_derive_last_exit_code_handles_empty_status(self) -> None:
        parsed = {"Type": "oneshot", "ExecMainStatus": ""}
        assert admin_services._derive_last_exit_code(parsed) is None


class TestControlUnitGatewayPushLock:
    """Gateway actions must use the installed helper as the sole lease owner."""

    GATEWAY = "radon-ib-gateway.service"

    @pytest.fixture(autouse=True)
    def _deploy_lock_path(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr(admin_services, "DEPLOY_LOCK_FILE", tmp_path / "deploy.lock")

    @staticmethod
    def _install_helper(tmp_path: Path, monkeypatch, *, rc: int = 0, detail: str = "ok") -> Path:
        log = tmp_path / "gateway-helper.log"
        helper = tmp_path / "radon-ib-gateway-control"
        helper.write_text(
            "#!/bin/sh\n"
            f"printf '%s\\n' \"$*\" >> '{log}'\n"
            f"printf '%s\\n' '{detail}' >&2\n"
            f"exit {rc}\n"
        )
        helper.chmod(0o755)
        monkeypatch.setattr(admin_services, "GATEWAY_CONTROL_PATH", str(helper))
        return log

    def _control(self, unit: str, action: str, systemctl_calls: list) -> admin_services.ActionResult:
        async def fake_systemctl(*args: str, **_kw: object) -> tuple[str, str, int]:
            systemctl_calls.append(args)
            return ("", "", 0)

        with patch.object(admin_services, "is_systemd_available", return_value=True), \
             patch.object(admin_services, "_systemctl", side_effect=fake_systemctl):
            return asyncio.run(admin_services.control_unit(unit, action))

    @pytest.mark.parametrize("action", ["start", "restart", "stop"])
    def test_gateway_action_routes_only_through_helper(
        self, tmp_path: Path, monkeypatch, action: str
    ) -> None:
        lock_path = tmp_path / "ib-2fa-push-lock.json"
        monkeypatch.setenv("IB_2FA_LOCK_PATH", str(lock_path))
        helper_log = self._install_helper(tmp_path, monkeypatch)
        calls: list = []
        result = self._control(self.GATEWAY, action, calls)

        assert result.ok is True
        assert helper_log.read_text().splitlines() == [action]
        assert calls == []
        assert not lock_path.exists(), "admin API must not pre-acquire the helper's lease"

    def test_helper_lease_refusal_maps_to_conflict(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        helper_log = self._install_helper(
            tmp_path,
            monkeypatch,
            rc=admin_services.GATEWAY_LEASE_HELD_RC,
            detail="REFUSING Gateway cycle: held holder=ib-watchdog",
        )
        calls: list = []
        result = self._control(self.GATEWAY, "restart", calls)

        assert result.ok is False
        assert result.returncode == admin_services.PUSH_LOCK_HELD_RC
        assert "ib-watchdog" in result.detail
        assert helper_log.read_text().splitlines() == ["restart"]
        assert calls == []

    def test_app_role_refuses_gateway_even_with_helper(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setenv("RADON_HOST_ROLE", "app")
        helper_log = self._install_helper(tmp_path, monkeypatch)
        calls: list = []
        result = self._control(self.GATEWAY, "restart", calls)

        assert result.ok is False
        assert result.returncode == -1
        assert "broker" in result.detail.lower()
        assert not helper_log.exists() or helper_log.read_text() == ""
        assert calls == []

    def test_helper_deploy_lock_refusal_maps_to_conflict(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        self._install_helper(
            tmp_path,
            monkeypatch,
            rc=admin_services.GATEWAY_CONTROL_BUSY_RC,
            detail="REFUSING Gateway mutation: deploy/control lock held",
        )
        calls: list = []

        result = self._control(self.GATEWAY, "restart", calls)

        assert result.ok is False
        assert result.returncode == admin_services.PUSH_LOCK_HELD_RC
        assert "deploy/control lock held" in result.detail
        assert calls == []

    def test_missing_helper_fails_closed(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr(
            admin_services,
            "GATEWAY_CONTROL_PATH",
            str(tmp_path / "missing-gateway-helper"),
        )
        calls: list = []
        result = self._control(self.GATEWAY, "restart", calls)

        assert result.ok is False
        assert "helper unavailable" in result.detail
        assert calls == []

    def test_timeout_kills_helper_descendants(self, tmp_path: Path, monkeypatch) -> None:
        marker = tmp_path / "orphan-survived"
        helper = tmp_path / "hanging-gateway-helper"
        helper.write_text(
            "#!/bin/sh\n"
            f"(trap '' TERM; sleep 0.5; printf survived > '{marker}') &\n"
            "trap '' TERM\n"
            "wait\n"
        )
        helper.chmod(0o755)
        monkeypatch.setattr(admin_services, "GATEWAY_CONTROL_PATH", str(helper))
        monkeypatch.setattr(admin_services, "GATEWAY_CONTROL_TIMEOUT_S", 0.05)
        monkeypatch.setattr(admin_services, "PROCESS_TERM_GRACE_S", 0.05)
        calls: list = []

        result = self._control(self.GATEWAY, "restart", calls)
        time.sleep(0.6)

        assert result.ok is False
        assert "timed out" in result.detail
        assert not marker.exists(), "timed-out helper descendant survived its process group"
        assert calls == []

    @pytest.mark.asyncio
    async def test_cancellation_kills_helper_descendants(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        marker = tmp_path / "cancelled-orphan-survived"
        helper = tmp_path / "cancelled-gateway-helper"
        helper.write_text(
            "#!/bin/sh\n"
            f"(trap '' TERM; sleep 0.5; printf survived > '{marker}') &\n"
            "trap '' TERM\n"
            "wait\n"
        )
        helper.chmod(0o755)
        monkeypatch.setattr(admin_services, "GATEWAY_CONTROL_PATH", str(helper))
        monkeypatch.setattr(admin_services, "PROCESS_TERM_GRACE_S", 0.05)

        task = asyncio.create_task(
            admin_services._run_gateway_helper("restart", timeout=60.0)
        )
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0.6)

        assert not marker.exists(), "cancelled helper descendant survived its process group"

    def test_non_gateway_units_are_never_gated(self) -> None:
        calls: list = []
        result = self._control("radon-api.service", "restart", calls)
        assert result.ok is True
        assert calls == [("restart", "radon-api.service")]

    def test_non_gateway_mutation_refuses_while_deploy_lock_is_held(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        lock_path = tmp_path / "held-deploy.lock"
        monkeypatch.setattr(admin_services, "DEPLOY_LOCK_FILE", lock_path)
        calls: list = []
        with lock_path.open("a+") as held:
            fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = self._control("radon-api.service", "restart", calls)

        assert result.ok is False
        assert result.returncode == admin_services.PUSH_LOCK_HELD_RC
        assert "deploy/control lock held" in result.detail
        assert calls == []

    def test_timed_out_systemctl_job_is_cancelled_before_lock_release(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        lock_path = tmp_path / "timeout-deploy.lock"
        monkeypatch.setattr(admin_services, "DEPLOY_LOCK_FILE", lock_path)
        calls = []

        async def fake_systemctl(*args: str, **_kwargs: object):
            calls.append(args)
            # Every cleanup subprocess must still run under the held lock.
            with lock_path.open("a+") as contender:
                with pytest.raises(BlockingIOError):
                    fcntl.flock(
                        contender.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB
                    )
            if args[0] == "restart":
                return "", "systemctl timed out", -1
            if args[0] == "list-jobs":
                return "42 radon-api.service start running", "", 0
            if args[0] == "cancel":
                return "", "", 0
            raise AssertionError(args)

        with patch.object(admin_services, "is_systemd_available", return_value=True), \
             patch.object(admin_services, "_systemctl", side_effect=fake_systemctl):
            result = asyncio.run(
                admin_services.control_unit("radon-api.service", "restart")
            )

        assert result.ok is False
        assert "cancelled pending systemd jobs 42" in result.detail
        assert calls == [
            ("restart", "radon-api.service"),
            ("list-jobs", "--no-legend", "--plain", "--no-pager"),
            ("cancel", "42"),
        ]
        with lock_path.open("a+") as after:
            fcntl.flock(after.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def test_list_units_filters_watchdog_only_gateway_adapter() -> None:
    async def fake_systemctl(*_args: str, **_kwargs: object) -> tuple[str, str, int]:
        return (
            "radon-api.service loaded active running API\n"
            "radon-ib-gateway-preheld-restart.service loaded inactive dead Internal",
            "",
            0,
        )

    with patch.object(admin_services, "is_systemd_available", return_value=True), \
         patch.object(admin_services, "_systemctl", side_effect=fake_systemctl):
        units = asyncio.run(admin_services.list_units())

    assert units == ["radon-api.service"]


class TestRestartFullStack:
    """Covers the new ``restart_full_stack`` wrapper around the operator CLI."""

    def test_refuses_when_operator_cli_missing(self) -> None:
        with patch.object(admin_services, "is_operator_cli_available", return_value=False):
            result = asyncio.run(admin_services.restart_full_stack())
        assert result.ok is False
        assert result.returncode == -1
        assert result.unit == "radon-stack"
        assert result.action == "restart"
        assert "operator CLI" in result.detail

    def test_happy_path_runs_radon_restart(self) -> None:
        class _FakeProc:
            returncode = 0

            async def communicate(self):
                return (b"restarted 24 radon units\n", b"")

        async def _fake_exec(*_args, **_kw):
            return _FakeProc()

        with patch.object(admin_services, "is_operator_cli_available", return_value=True), \
             patch.object(admin_services.asyncio, "create_subprocess_exec", side_effect=_fake_exec):
            result = asyncio.run(admin_services.restart_full_stack())
        assert result.ok is True
        assert result.returncode == 0
        assert result.unit == "radon-stack"
        assert "restarted 24 radon units" in result.detail

    def test_non_zero_exit_returns_failure(self) -> None:
        class _FakeProc:
            returncode = 1

            async def communicate(self):
                return (b"", b"radon stop failed: permission denied\n")

        async def _fake_exec(*_args, **_kw):
            return _FakeProc()

        with patch.object(admin_services, "is_operator_cli_available", return_value=True), \
             patch.object(admin_services.asyncio, "create_subprocess_exec", side_effect=_fake_exec):
            result = asyncio.run(admin_services.restart_full_stack())
        assert result.ok is False
        assert result.returncode == 1
        assert "permission denied" in result.detail

    def test_timeout_kills_full_stack_descendants(self, tmp_path: Path, monkeypatch) -> None:
        marker = tmp_path / "stack-orphan-survived"
        operator = tmp_path / "hanging-radon"
        operator.write_text(
            "#!/bin/sh\n"
            f"(trap '' TERM; sleep 0.5; printf survived > '{marker}') &\n"
            "trap '' TERM\n"
            "wait\n"
        )
        operator.chmod(0o755)
        monkeypatch.setattr(admin_services, "OPERATOR_CLI_PATH", str(operator))
        monkeypatch.setattr(admin_services, "STACK_RESTART_TIMEOUT_S", 0.05)
        monkeypatch.setattr(admin_services, "PROCESS_TERM_GRACE_S", 0.05)

        with patch.object(admin_services, "is_operator_cli_available", return_value=True):
            result = asyncio.run(admin_services.restart_full_stack())
        time.sleep(0.6)

        assert result.ok is False
        assert result.returncode == -1
        assert "timed out" in result.detail.lower()
        assert not marker.exists(), "timed-out full-stack descendant survived its process group"


# ---------------------------------------------------------------------------
# REL-171 (R-474, R-499, R-500, R-501): the remote status probe is cheap and
# coalesced; deadlines are monotonic; remote failures are structured.
# ---------------------------------------------------------------------------


def _configure_app_remote(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("RADON_HOST_ROLE", "app")
    for name in ("ca.pem", "client.pem", "client.key"):
        (tmp_path / name).write_text("x")
    monkeypatch.setenv("RADON_IB_REMOTE_URL", "https://10.0.0.4:8340")
    monkeypatch.setenv("RADON_IB_REMOTE_CA", str(tmp_path / "ca.pem"))
    monkeypatch.setenv("RADON_IB_REMOTE_CLIENT_CERT", str(tmp_path / "client.pem"))
    monkeypatch.setenv("RADON_IB_REMOTE_CLIENT_KEY", str(tmp_path / "client.key"))
    monkeypatch.setattr(admin_services, "is_systemd_available", lambda: False)
    if hasattr(admin_services, "_reset_remote_status_cache"):
        admin_services._reset_remote_status_cache()


class TestRemoteStatusProbeBounds:
    def test_hung_broker_does_not_block_the_services_poll(self, monkeypatch, tmp_path: Path) -> None:
        import threading

        _configure_app_remote(monkeypatch, tmp_path)
        release = threading.Event()
        calls: list[tuple[str, float]] = []

        def hung(verb: str, timeout: float):
            calls.append((verb, timeout))
            release.wait(20)
            return 200, {"ok": True, "state": "running", "detail": "running"}

        monkeypatch.setattr(admin_services, "_remote_http", hung)
        try:

            async def ten_polls():
                return await asyncio.gather(
                    *(admin_services.list_units_with_status() for _ in range(10))
                )

            started = time.monotonic()
            batches = asyncio.run(ten_polls())
            elapsed = time.monotonic() - started
            assert elapsed < 8.0, f"services poll blocked {elapsed:.1f}s behind a hung broker"
            gateway_rows = [
                u for batch in batches for u in batch if u.unit == admin_services.GATEWAY_UNIT
            ]
            assert len(gateway_rows) == 10
            assert {u.active_state for u in gateway_rows} == {"unknown"}
            assert len(calls) == 1, f"{len(calls)} probes in flight for ten concurrent polls"
            assert calls[0][1] <= 5.0

            # A later poll while the first probe is still hung coalesces onto it.
            asyncio.run(admin_services.show_unit(admin_services.GATEWAY_UNIT))
            assert len(calls) == 1
        finally:
            release.set()

    def test_status_probe_result_is_cached_briefly(self, monkeypatch, tmp_path: Path) -> None:
        _configure_app_remote(monkeypatch, tmp_path)
        calls: list[str] = []

        def fast(verb: str, timeout: float):
            calls.append(verb)
            return 200, {"ok": True, "state": "running", "detail": "running"}

        monkeypatch.setattr(admin_services, "_remote_http", fast)
        first = asyncio.run(admin_services.show_unit(admin_services.GATEWAY_UNIT))
        second = asyncio.run(admin_services.show_unit(admin_services.GATEWAY_UNIT))
        assert first.active_state == second.active_state == "active"
        assert len(calls) == 1

    def test_deadlines_are_monotonic_across_the_split(self) -> None:
        from ib_gateway_remote import serve

        assert admin_services.REMOTE_STATUS_TIMEOUT_S <= 5.0
        assert admin_services.REMOTE_TIMEOUT_S >= serve.HELPER_TIMEOUT_S + 15


class TestRemoteFailuresAreStructured:
    def test_unreachable_broker_is_a_gateway_timeout_not_a_client_error(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        from fastapi.testclient import TestClient

        from scripts.api import auth, server

        _configure_app_remote(monkeypatch, tmp_path)
        monkeypatch.setattr(auth, "is_trusted_local_request", lambda request: True)
        monkeypatch.setattr(server, "is_trusted_local_request", lambda request: True)
        monkeypatch.setattr(server, "_is_app_role_gateway_mutation", lambda request: False)
        # server.app binds `api.services`, a distinct module object from the
        # `scripts.api.services` this file imports; patch the one the app uses.
        for mod in {admin_services, server.admin_services}:
            monkeypatch.setattr(
                mod,
                "_remote_http",
                lambda verb, timeout: (-1, {"ok": False, "detail": "timed out"}),
            )
            monkeypatch.setattr(mod, "is_systemd_available", lambda: False)
        resp = TestClient(server.app).post(
            f"/admin/services/{admin_services.GATEWAY_UNIT}/start"
        )
        assert resp.status_code in {502, 504}, resp.text
        assert resp.json()["detail"]["returncode"] != -1

    def test_non_numeric_returncode_is_a_structured_failure(self, monkeypatch, tmp_path: Path) -> None:
        _configure_app_remote(monkeypatch, tmp_path)
        monkeypatch.setattr(
            admin_services,
            "_remote_http",
            lambda verb, timeout: (200, {"ok": False, "returncode": "busy"}),
        )
        result = asyncio.run(admin_services.remote_gateway_action("start"))
        assert result.ok is False
        assert result.returncode not in {0, -1}

    def test_undecodable_body_is_a_structured_failure(self, monkeypatch, tmp_path: Path) -> None:
        import urllib.request

        _configure_app_remote(monkeypatch, tmp_path)

        class _Resp:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return b"\xff\xfe\x00"

        monkeypatch.setattr(admin_services, "_remote_ssl_context", lambda: None)
        monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _Resp())
        status, payload = admin_services._remote_http("start", 1.0)
        assert payload["ok"] is False
        assert "detail" in payload
