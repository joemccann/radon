"""R-382 / R-397 / R-398 / REL-135: a dependency failure that does not clear pages.

`35071d85` and `f7b5eeb9` moved `radon-newsfeed.service` and
`radon-monitor.service` into `DEPENDENCY_UNITS` to absorb a `Restart=always`
flap. That was right for a flap and wrong for a death: `aggregate_state` is a
pure function of ONE instantaneous snapshot, so a unit that died two seconds ago
and one that has been `failed` for a week are the same input, and `degraded`
converts off-box to `{"ok": 1, "detail": "edge_ok:aggregate_degraded"}` -- an
explicit non-page. `radon-monitor` is the fill / order / journal daemon.

R-397: `unit_coarse_state(active_state, sub_state)` took `sub_state` and never
read it, so a crash loop (`activating`/`auto-restart`) and a normal start
(`activating`/`start-pre`) were the same input.

R-398: the dependency-downish branch sat ABOVE the serving-path `starting`
check, so a failed sidecar swallowed a serving-path verdict.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from health_service import probes, serve  # noqa: E402
from health_probe import probe as edge_probe  # noqa: E402

UP_PROBES = {
    "api": {"state": "up"},
    "relay": {"state": "up"},
    "nextjs": {"state": "up"},
}

# T-460: every verdict here pins host_role explicitly. With the parameter
# omitted, probes.resolve_host_role() reads RADON_HOST_ROLE from the ambient
# environment, so this suite only held on hosts where scripts/conftest.py's
# env scrub ran — a split-role host exporting `app` flipped
# test_broker_weekend_clean_exit_does_not_escalate_past_dwell to "up".
COMBINED = "combined"


def _units(**states) -> dict:
    return {name.replace("__", "-") + ".service": dict(value) for name, value in states.items()}


class TestDependencyDwellBound:
    def test_a_flap_is_absorbed_but_a_death_escalates(self):
        """degraded at t+60s, down at t+16min. Same snapshot, different dwell."""
        flap = {"radon-monitor.service": {"state": "down", "non_up_secs": 60.0}}
        death = {"radon-monitor.service": {"state": "down", "non_up_secs": 16 * 60.0}}

        assert probes.aggregate_state(UP_PROBES, flap, "ok", 2.0, host_role=COMBINED) == "degraded"
        assert probes.aggregate_state(UP_PROBES, death, "ok", 2.0, host_role=COMBINED) == "down"

    def test_the_escalated_aggregate_pages_off_box(self):
        death = {"radon-monitor.service": {"state": "down", "non_up_secs": 16 * 60.0}}
        payload = probes.build_status(
            UP_PROBES, death, "2026-08-29T00:00:00Z", "ok", 2.0, host_role=COMBINED
        )
        verdict = edge_probe.classify_probes(
            {"reachable": True, "http_status": 200},
            {"reachable": True, "http_status": 200, "payload": payload},
        )
        assert verdict == {"ok": 0, "detail": "aggregate_down"}

    def test_a_sixty_second_newsfeed_flap_still_does_not_page(self):
        """The 2026-08-29 fix this builds on must stay green."""
        flap = {"radon-newsfeed.service": {"state": "starting", "non_up_secs": 60.0}}
        payload = probes.build_status(
            UP_PROBES, flap, "2026-08-29T00:00:00Z", "ok", 2.0, host_role=COMBINED
        )
        assert payload["overall_state"] == "degraded"
        verdict = edge_probe.classify_probes(
            {"reachable": True, "http_status": 200},
            {"reachable": True, "http_status": 200, "payload": payload},
        )
        assert verdict["ok"] == 1

    def test_a_permanent_crash_loop_escalates_too(self):
        """A unit that never reaches active is non-up the whole time."""
        loop = {"radon-newsfeed.service": {"state": "down", "non_up_secs": 20 * 60.0}}
        assert probes.aggregate_state(UP_PROBES, loop, "ok", 2.0, host_role=COMBINED) == "down"

    def test_a_dependency_with_no_dwell_field_is_unchanged(self):
        """Missing telemetry must not invent an escalation."""
        legacy = {"radon-monitor.service": {"state": "down"}}
        assert probes.aggregate_state(UP_PROBES, legacy, "ok", 2.0, host_role=COMBINED) == "degraded"

    def test_broker_weekend_clean_exit_does_not_escalate_past_dwell(self):
        """2026-08-30 page a45d6410: IBKR weekend session shutdown leaves
        radon-ib-gateway inactive/dead Result=success for hours. Serving path
        stays up. R-382 dwell treated the broker like a sidecar death and
        collapsed the aggregate to down, so the off-box observer paged
        aggregate_down. The broker already has on-box ib-gateway-grouped; the
        edge must stay degraded, not down, even after 16 minutes.
        """
        weekend = {
            "radon-api.service": {"state": "up"},
            "radon-relay.service": {"state": "up"},
            "radon-nextjs.service": {"state": "up"},
            "radon-monitor.service": {"state": "up"},
            "radon-newsfeed.service": {"state": "up"},
            "radon-ib-gateway.service": {
                "state": "down",
                "sub_state": "dead",
                "result": "success",
                "non_up_secs": 16 * 60.0,
            },
        }
        live_probes = {
            "radon-api": {
                "state": "up",
                "payload": {
                    "status": "ok",
                    "auth_state": "authenticated",
                    "service_state": "reachable",
                    "upstream_dead": False,
                    "port_listening": True,
                },
            },
            "radon-relay": {"state": "up"},
            "radon-nextjs": {"state": "up"},
            "ib-gateway": {"state": "down", "detail": "ConnectionRefusedError"},
        }
        # REL-181 rewrote the suppression from a set-exclusion to a
        # calendar+result predicate, so this case now pins the injected
        # Saturday clock the scenario always described (page a45d6410 WAS a
        # weekend). The unconditional form this test used to accept is R-478.
        from datetime import datetime
        from zoneinfo import ZoneInfo

        saturday = datetime(2026, 8, 30, 22, 6, tzinfo=ZoneInfo("America/New_York"))
        assert probes.aggregate_state(
            live_probes, weekend, "ok", 2.0, now_et=saturday, host_role=COMBINED
        ) == "degraded"
        payload = probes.build_status(
            live_probes, weekend, "2026-08-30T22:06:00Z", "ok", 2.0, host_role=COMBINED,
            now_et=saturday,
        )
        assert payload["overall_state"] == "degraded"
        verdict = edge_probe.classify_probes(
            {"reachable": True, "http_status": 200},
            {"reachable": True, "http_status": 200, "payload": payload},
        )
        assert verdict["ok"] == 1
        assert verdict["detail"].startswith("edge_ok:aggregate_degraded")

    def test_dwell_escalation_covers_the_broker_under_a_predicate(self):
        """REL-181 (R-478): the broker is dwell-escalated like every other
        dependency unless BOTH the market is closed and the exit was clean."""
        assert "radon-ib-gateway.service" in probes.DWELL_ESCALATE_UNITS
        assert probes.DWELL_ESCALATE_UNITS == probes.DEPENDENCY_UNITS


class TestUnitStateCacheCarriesDwell:
    def test_the_cache_stamps_first_seen_and_ages_it(self, monkeypatch):
        clock = {"t": 1000.0}
        cache = serve.UnitStateCache(["radon-monitor.service"])
        monkeypatch.setattr(serve.time, "time", lambda: clock["t"])

        block = (
            "Id=radon-monitor.service\nActiveState=failed\n"
            "SubState=failed\nResult=exit-code\n"
        )
        monkeypatch.setattr(
            serve.subprocess,
            "run",
            lambda *a, **k: type("R", (), {"returncode": 0, "stdout": block})(),
        )

        cache.refresh_once()
        value, _age = cache.snapshot()
        assert value["radon-monitor.service"]["non_up_secs"] == 0.0

        clock["t"] += 16 * 60
        cache.refresh_once()
        value, _age = cache.snapshot()
        assert value["radon-monitor.service"]["non_up_secs"] == pytest.approx(960.0)

    def test_recovery_clears_the_dwell(self, monkeypatch):
        clock = {"t": 1000.0}
        cache = serve.UnitStateCache(["radon-monitor.service"])
        monkeypatch.setattr(serve.time, "time", lambda: clock["t"])
        state = {"body": "Id=radon-monitor.service\nActiveState=failed\nSubState=failed\nResult=exit-code\n"}
        monkeypatch.setattr(
            serve.subprocess,
            "run",
            lambda *a, **k: type("R", (), {"returncode": 0, "stdout": state["body"]})(),
        )

        cache.refresh_once()
        clock["t"] += 16 * 60
        cache.refresh_once()
        assert cache.snapshot()[0]["radon-monitor.service"]["non_up_secs"] > 900

        state["body"] = "Id=radon-monitor.service\nActiveState=active\nSubState=running\nResult=success\n"
        cache.refresh_once()
        assert cache.snapshot()[0]["radon-monitor.service"]["non_up_secs"] is None

        state["body"] = "Id=radon-monitor.service\nActiveState=failed\nSubState=failed\nResult=exit-code\n"
        cache.refresh_once()
        assert cache.snapshot()[0]["radon-monitor.service"]["non_up_secs"] == 0.0


class TestCrashLoopIsNotAStart:
    def test_auto_restart_is_down_not_starting(self):
        raw = (
            "Id=radon-newsfeed.service\nActiveState=activating\n"
            "SubState=auto-restart\nResult=exit-code\n"
        )
        parsed = probes.parse_unit_states(raw)
        assert parsed["radon-newsfeed.service"]["state"] == "down"

    def test_an_ordinary_start_is_still_starting(self):
        raw = (
            "Id=radon-newsfeed.service\nActiveState=activating\n"
            "SubState=start-pre\nResult=success\n"
        )
        parsed = probes.parse_unit_states(raw)
        assert parsed["radon-newsfeed.service"]["state"] == "starting"


class TestServingVerdictIsNotSwallowed:
    def test_a_sidecar_failure_cannot_hide_a_serving_path_start(self):
        with_sidecar = {
            "radon-nextjs.service": {"state": "starting"},
            "radon-newsfeed.service": {"state": "down"},
        }
        without_sidecar = {"radon-nextjs.service": {"state": "starting"}}

        assert probes.aggregate_state(UP_PROBES, with_sidecar, "ok", 2.0, host_role=COMBINED) == "starting"
        assert probes.aggregate_state(UP_PROBES, without_sidecar, "ok", 2.0, host_role=COMBINED) == "starting"
