"""DUR-10: second sensor, quiet windows, bounded hrana health-write,
per-step cycle timing.

Covers:
  1. The second-sensor decision table (api up/down x gateway alive/dead/
     wedged/unknown). When FastAPI /health is unreachable the watchdog
     falls back to a DIRECT bounded gateway probe (TCP connect + IB API
     handshake) instead of going blind.
  2. "api unreachable" alone can NEVER mint a new restart trigger —
     deploys produce Connection refused routinely and every gateway
     restart costs a 2FA push. The fallback may only CONTINUE an
     existing api-hang episode when the handshake itself shows
     upstream-dead.
  3. The scheduled-restart quiet windows (default 23:40-00:15 UTC for
     the gateway's built-in 23:45 UTC auto-restart, 09:00-09:30 UTC for
     the pending dur-08 patch's 09:05 restart): detections are logged
     but degraded_count does not advance and no 2FA push fires.
  4. service_health writes go through the bounded stdlib hrana
     transport (scripts/db/hrana_http), never sync libsql (db.writer) —
     the suspected source of the watchdog's post-refused 60s hangs.
  5. One per-step duration summary line per cycle.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import ib_watchdog  # type: ignore[import-not-found]
from ib_watchdog import (  # type: ignore[import-not-found]
    GATEWAY_ALIVE,
    GATEWAY_DEAD,
    GATEWAY_UNKNOWN,
    GATEWAY_WEDGED,
    GatewayState,
    WatchdogState,
    quiet_window_active,
    run_cycle,
    save_state,
)


@pytest.fixture(autouse=True)
def _redirect_2fa_lock_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IB_2FA_LOCK_PATH", str(tmp_path / "ib-2fa-push-lock.json"))


@pytest.fixture
def state_path(tmp_path: Path) -> Path:
    return tmp_path / "watchdog-state.json"


def _utc(hour: int, minute: int) -> datetime:
    return datetime(2026, 6, 12, hour, minute, tzinfo=timezone.utc)


NOON = _utc(12, 0)  # comfortably outside both default quiet windows


def _payload(
    *,
    service_state: str = "healthy",
    port_listening: bool = True,
    upstream_dead: bool = False,
    auth_state: str = "authenticated",
) -> dict:
    return {
        "ib_gateway": {
            "service_state": service_state,
            "port_listening": port_listening,
            "upstream_dead": upstream_dead,
            "auth_state": auth_state,
        }
    }


def _hang_payload() -> dict:
    return _payload(
        service_state="unhealthy", upstream_dead=True, auth_state="authenticated"
    )


def _stuck_2fa_payload() -> dict:
    # Genuine stuck-2FA: upstream_dead False (container healthy, parked at the
    # prompt). upstream_dead=True is the JVM acceptor hang → api-hang path.
    return _payload(
        service_state="unhealthy", upstream_dead=False, auth_state="awaiting_2fa"
    )


def _drive(
    state_path: Path,
    payload: dict | None,
    *,
    gateway_probe: str = GATEWAY_UNKNOWN,
    attribution: str = "radon_api_down",
    now: datetime = NOON,
    **kwargs,
):
    """One cycle with /health, the direct gateway probe, and the :8330
    attribution all mocked. ``payload=None`` = /health unreachable."""

    def fake_fetch(url: str, timeout: float):
        if payload is None:
            return None
        return GatewayState.from_health_payload(payload)

    health_mock = MagicMock()
    with (
        patch("ib_watchdog.fetch_health", side_effect=fake_fetch),
        patch("ib_watchdog.trigger_restart", return_value=True) as restart_mock,
        patch("ib_watchdog.record_service_health", health_mock),
        patch("ib_watchdog.probe_gateway_direct", return_value=gateway_probe) as probe_mock,
        patch("ib_watchdog.attribute_api_down", return_value=attribution),
    ):
        result = run_cycle(
            state_path=state_path, dry_run=True, utcnow=lambda: now, **kwargs
        )
    return result, restart_mock, probe_mock, health_mock


# --- 1. second-sensor decision table -----------------------------------------


class TestDecisionTableApiUp:
    """api up: the primary sensor rules; the direct probe must NOT run."""

    def test_gateway_alive_resets_counters(self, state_path):
        save_state(state_path, WatchdogState(degraded_count=2))
        result, restart, probe, _ = _drive(state_path, _payload())
        assert result.degraded_count == 0
        restart.assert_not_called()
        probe.assert_not_called()

    def test_gateway_dead_port_down_resets(self, state_path):
        # Docker restart policy owns port-down; not an api-hang.
        save_state(state_path, WatchdogState(degraded_count=2))
        result, restart, probe, _ = _drive(
            state_path,
            _payload(service_state="unhealthy", port_listening=False,
                     upstream_dead=True, auth_state="unreachable"),
        )
        assert result.degraded_count == 0
        restart.assert_not_called()
        probe.assert_not_called()

    def test_gateway_wedged_increments_and_restarts_at_threshold(self, state_path):
        save_state(state_path, WatchdogState(degraded_count=2))
        result, restart, probe, _ = _drive(state_path, _hang_payload())
        assert restart.call_count == 1
        assert result.degraded_count == 0  # reset after restart
        probe.assert_not_called()


class TestDecisionTableApiDown:
    """api down: the direct gateway probe is the only sensor left."""

    def test_gateway_alive_resets_episode_and_names_radon_api(self, state_path):
        save_state(state_path, WatchdogState(degraded_count=2))
        result, restart, probe, health = _drive(
            state_path, None, gateway_probe=GATEWAY_ALIVE
        )
        assert result.degraded_count == 0
        restart.assert_not_called()
        probe.assert_called_once()
        # service_health row must name radon-api as the broken sensor.
        messages = [
            kwargs.get("error_message") or (args[1] if len(args) > 1 else None)
            for args, kwargs in health.call_args_list
        ]
        assert any(m and "radon-api" in m for m in messages)

    def test_gateway_dead_resets_episode(self, state_path):
        # TCP refused = port down = Docker restart policy's job.
        save_state(state_path, WatchdogState(degraded_count=2))
        result, restart, _, _ = _drive(state_path, None, gateway_probe=GATEWAY_DEAD)
        assert result.degraded_count == 0
        restart.assert_not_called()
        assert "port_down" in result.last_outcome

    def test_gateway_wedged_continues_existing_episode(self, state_path):
        save_state(state_path, WatchdogState(degraded_count=1))
        result, restart, _, _ = _drive(state_path, None, gateway_probe=GATEWAY_WEDGED)
        assert result.degraded_count == 2
        restart.assert_not_called()

    def test_gateway_wedged_completes_restart_at_threshold(self, state_path):
        # The handshake itself shows upstream-dead — continuation may
        # carry an existing episode over the line.
        save_state(state_path, WatchdogState(degraded_count=2))
        result, restart, _, _ = _drive(state_path, None, gateway_probe=GATEWAY_WEDGED)
        assert restart.call_count == 1
        assert result.degraded_count == 0

    def test_gateway_unknown_freezes_counter(self, state_path):
        # Connect timeout etc. — can't tell. Leave the episode where it is.
        save_state(state_path, WatchdogState(degraded_count=2))
        result, restart, _, _ = _drive(state_path, None, gateway_probe=GATEWAY_UNKNOWN)
        assert result.degraded_count == 2
        restart.assert_not_called()

    def test_fallback_resets_stuck_2fa_counter(self, state_path):
        # Without /health we can't see auth_state; never age the 2FA
        # counter across an api outage (pre-DUR-10 behavior preserved).
        save_state(state_path, WatchdogState(stuck_2fa_count=2))
        result, _, _, _ = _drive(state_path, None, gateway_probe=GATEWAY_WEDGED)
        assert result.stuck_2fa_count == 0


# --- 2. api unreachable can never mint a NEW restart trigger -----------------


class TestNoNewTriggerWhileBlind:
    def test_wedged_with_no_episode_never_starts_counting(self, state_path):
        for _ in range(5):
            result, restart, _, _ = _drive(
                state_path, None, gateway_probe=GATEWAY_WEDGED
            )
            assert result.degraded_count == 0
            restart.assert_not_called()

    def test_repeated_api_down_cycles_never_restart(self, state_path):
        # A deploy window: /health refused for many cycles, gateway state
        # ambiguous. Nothing may fire.
        for verdict in (GATEWAY_UNKNOWN, GATEWAY_DEAD, GATEWAY_ALIVE, GATEWAY_UNKNOWN):
            _, restart, _, _ = _drive(state_path, None, gateway_probe=verdict)
            restart.assert_not_called()


# --- 3. scheduled-restart quiet windows --------------------------------------


class TestQuietWindowParsing:
    @pytest.fixture(autouse=True)
    def _default_windows(self, monkeypatch):
        # conftest neutralizes the windows for determinism; these tests
        # exercise the shipped default spec, so pin it back explicitly.
        monkeypatch.setenv(
            ib_watchdog.QUIET_WINDOWS_ENV, ib_watchdog.DEFAULT_QUIET_WINDOWS_UTC
        )

    def test_default_windows_cover_both_scheduled_restarts(self):
        # 23:45 UTC (current IBC default) and 09:05 UTC (pending dur-08 patch).
        assert quiet_window_active(_utc(23, 45)) is True
        assert quiet_window_active(_utc(9, 5)) is True

    def test_first_window_wraps_midnight(self):
        assert quiet_window_active(_utc(23, 40)) is True
        assert quiet_window_active(_utc(0, 10)) is True
        assert quiet_window_active(_utc(0, 15)) is False  # exclusive end

    def test_outside_windows_is_inactive(self):
        assert quiet_window_active(NOON) is False
        assert quiet_window_active(_utc(9, 30)) is False
        assert quiet_window_active(_utc(8, 59)) is False

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("RADON_GW_RESTART_QUIET_WINDOWS_UTC", "09:00-09:30")
        assert quiet_window_active(_utc(23, 45)) is False
        assert quiet_window_active(_utc(9, 5)) is True

    def test_empty_env_disables_all_windows(self, monkeypatch):
        monkeypatch.setenv("RADON_GW_RESTART_QUIET_WINDOWS_UTC", "")
        assert quiet_window_active(_utc(23, 45)) is False

    def test_garbage_entries_are_skipped(self, monkeypatch):
        monkeypatch.setenv(
            "RADON_GW_RESTART_QUIET_WINDOWS_UTC", "garbage,09:00-09:30"
        )
        assert quiet_window_active(_utc(9, 5)) is True
        assert quiet_window_active(_utc(23, 45)) is False


class TestQuietWindowSuppression:
    @pytest.fixture(autouse=True)
    def _default_windows(self, monkeypatch):
        monkeypatch.setenv(
            ib_watchdog.QUIET_WINDOWS_ENV, ib_watchdog.DEFAULT_QUIET_WINDOWS_UTC
        )

    @pytest.mark.parametrize("now", [_utc(23, 50), _utc(0, 5), _utc(9, 10)])
    def test_hang_does_not_advance_counter_in_window(self, state_path, now):
        save_state(state_path, WatchdogState(degraded_count=1))
        result, restart, _, _ = _drive(state_path, _hang_payload(), now=now)
        assert result.degraded_count == 1  # frozen, NOT advanced
        restart.assert_not_called()
        assert "quiet_window" in result.last_outcome

    def test_no_restart_even_at_threshold_in_window(self, state_path):
        save_state(state_path, WatchdogState(degraded_count=2))
        result, restart, _, _ = _drive(
            state_path, _hang_payload(), now=_utc(23, 50)
        )
        restart.assert_not_called()
        assert result.degraded_count == 2

    def test_stuck_2fa_push_is_frozen_in_window(self, state_path):
        # No 2FA push may be initiated by the watchdog during the window.
        save_state(state_path, WatchdogState(stuck_2fa_count=2))
        result, restart, _, _ = _drive(
            state_path, _stuck_2fa_payload(), now=_utc(9, 10)
        )
        assert result.stuck_2fa_count == 2  # frozen
        restart.assert_not_called()

    def test_fallback_wedged_continuation_is_frozen_in_window(self, state_path):
        save_state(state_path, WatchdogState(degraded_count=2))
        result, restart, _, _ = _drive(
            state_path, None, gateway_probe=GATEWAY_WEDGED, now=_utc(23, 50)
        )
        assert result.degraded_count == 2
        restart.assert_not_called()

    def test_same_hang_counts_normally_outside_window(self, state_path):
        save_state(state_path, WatchdogState(degraded_count=1))
        result, _, _, _ = _drive(state_path, _hang_payload(), now=NOON)
        assert result.degraded_count == 2

    def test_episode_resumes_counting_after_window(self, state_path):
        # In-window detections freeze; the first out-of-window cycle
        # continues from the frozen value.
        save_state(state_path, WatchdogState(degraded_count=1))
        _drive(state_path, _hang_payload(), now=_utc(23, 50))
        result, _, _, _ = _drive(state_path, _hang_payload(), now=_utc(0, 20))
        assert result.degraded_count == 2


# --- 4. health-write transport: bounded stdlib hrana, never sync libsql ------


class TestHealthWriteTransport:
    def test_record_service_health_uses_canonical_sql_over_hrana(self, monkeypatch):
        from db import hrana_http  # type: ignore[import-not-found]
        from db.service_health_sql import SERVICE_HEALTH_UPSERT_SQL  # type: ignore[import-not-found]

        calls = []
        monkeypatch.setattr(
            hrana_http,
            "hrana_execute",
            lambda sql, args=(), timeout=None: calls.append((sql, tuple(args))) or [],
        )
        ib_watchdog.record_service_health("ok", error_message="msg")
        assert len(calls) == 1
        sql, args = calls[0]
        assert sql == SERVICE_HEALTH_UPSERT_SQL
        assert args[0] == "ib-watchdog"
        assert args[1] == "ok"

    def test_health_write_is_bounded_when_transport_hangs(self, monkeypatch):
        import time as _time

        from db import hrana_http  # type: ignore[import-not-found]

        monkeypatch.setattr(
            hrana_http, "hrana_execute", lambda *a, **k: _time.sleep(30)
        )
        monkeypatch.setattr(ib_watchdog, "SERVICE_HEALTH_WRITE_TIMEOUT_SECS", 0.2)
        started = _time.monotonic()
        ib_watchdog.record_service_health("ok")  # must not raise / hang
        assert _time.monotonic() - started < 5

    def test_watchdog_never_imports_sync_libsql_writer(self):
        import ast

        tree = ast.parse(Path(ib_watchdog.__file__).read_text())
        banned = {"db.writer", "scripts.db.writer", "db.client", "scripts.db.client"}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                assert module not in banned, f"sync libsql import: from {module}"
                assert not module.startswith("libsql"), f"libsql import: {module}"
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name not in banned
                    assert not alias.name.startswith("libsql")

    def test_hrana_refuses_real_connections_under_pytest(self, monkeypatch):
        # Mirror of the db.client test-pollution guard
        # (feedback_test_pollution_to_production).
        from db import hrana_http  # type: ignore[import-not-found]

        monkeypatch.setenv("TURSO_DB_URL", "libsql://example.turso.io")
        monkeypatch.setenv("TURSO_AUTH_TOKEN", "tok")
        monkeypatch.delenv("RADON_DB_TEST_WRITE_OK", raising=False)
        with pytest.raises(hrana_http.HranaHttpError, match="pytest"):
            hrana_http.hrana_execute("SELECT 1")


# --- 5. one per-step duration summary line per cycle --------------------------


class TestStepSummaryLog:
    def test_cycle_emits_single_step_summary_line(self, state_path, caplog):
        def fake_fetch(url, timeout):
            return GatewayState.from_health_payload(_payload())

        with (
            patch("ib_watchdog.fetch_health", side_effect=fake_fetch),
            patch("ib_watchdog.trigger_restart", return_value=True),
            patch("ib_watchdog._write_service_health"),
            caplog.at_level("INFO", logger="ib_watchdog"),
        ):
            run_cycle(state_path=state_path, dry_run=True, utcnow=lambda: NOON)

        summaries = [r.message for r in caplog.records if "cycle steps:" in r.message]
        assert len(summaries) == 1, f"expected one summary line, got {summaries}"
        line = summaries[0]
        for token in ("probe=", "health_write=", "total=", "outcome="):
            assert token in line, f"missing {token!r} in {line!r}"

    def test_summary_includes_direct_probe_on_fallback(self, state_path, caplog):
        with (
            patch("ib_watchdog.fetch_health", return_value=None),
            patch("ib_watchdog.probe_gateway_direct", return_value=GATEWAY_UNKNOWN),
            patch("ib_watchdog.attribute_api_down", return_value="attribution_unavailable"),
            patch("ib_watchdog._write_service_health"),
            caplog.at_level("INFO", logger="ib_watchdog"),
        ):
            run_cycle(state_path=state_path, dry_run=True, utcnow=lambda: NOON)

        line = next(r.message for r in caplog.records if "cycle steps:" in r.message)
        assert "direct_probe=" in line
