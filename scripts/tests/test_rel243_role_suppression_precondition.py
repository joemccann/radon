"""REL-243 (R-650, NF-10): the app-role gateway suppression needs a positive
precondition or a dwell bound.

`ROLE_NOT_APPLICABLE` dropped `ib-gateway` / `radon-ib-gateway.service` from
the aggregate unconditionally on `RADON_HOST_ROLE=app`. Copy that env onto a
host that IS running a local gateway and the :4001 probe never enters the
aggregate again: edge-green with a dead gateway, forever. The exclusion now
holds while the nested `radon-api:broker` probe is observed up, or while the
local unit is clean-inactive Result=success (true app host). A local crash
(not Result=success) degrades to counted after the existing 900s dependency
dwell (`DEPENDENCY_DWELL_LIMIT_SECS`), and /status acknowledges the expiry.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

SCRIPTS = Path(__file__).resolve().parent.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from health_probe import probe as edge_probe  # noqa: E402
from health_service import probes  # noqa: E402

ET = ZoneInfo("America/New_York")
TUESDAY_1000_ET = datetime(2026, 9, 8, 10, 0, tzinfo=ET)
# 2026-09-15 page 3a6de316: off-box aggregate_down at 19:50 ET, still inside
# the 04:00-20:00 EXT window so the overnight Result=success predicate does
# not hold, while the app-host local gateway has been clean-inactive for days.
TUESDAY_1950_ET = datetime(2026, 9, 15, 19, 50, tzinfo=ET)

PAST_DWELL = probes.DEPENDENCY_DWELL_LIMIT_SECS + 1.0
WITHIN_DWELL = probes.DEPENDENCY_DWELL_LIMIT_SECS / 3.0


def _probes(broker_up: bool, with_payload: bool = True) -> dict:
    api = {"state": "up"}
    if with_payload:
        api["payload"] = {
            "service_state": "reachable",
            "auth_state": "authenticated" if broker_up else "unauthenticated",
            "upstream_dead": not broker_up,
            "port_listening": broker_up,
        }
    return {
        "radon-api": api,
        "radon-relay": {"state": "up"},
        "radon-nextjs": {"state": "up"},
        "ib-gateway": {"state": "down", "detail": "ConnectionRefusedError"},
    }


def _units(gateway_dwell: float, result: str = "exit-code") -> dict:
    return {
        "radon-api.service": {"state": "up"},
        "radon-relay.service": {"state": "up"},
        "radon-nextjs.service": {"state": "up"},
        "radon-monitor.service": {"state": "up"},
        "radon-newsfeed.service": {"state": "up"},
        "radon-ib-gateway.service": {
            "state": "down",
            "sub_state": "dead",
            "result": result,
            "non_up_secs": gateway_dwell,
        },
    }


class TestSuppressionPrecondition:
    def test_broker_down_past_dwell_counts_the_gateway(self, monkeypatch):
        """role=app, local gateway unit down, nested broker probe down: past
        the dwell the gateway must survive into the aggregate."""
        monkeypatch.setenv("RADON_HOST_ROLE", "app")
        body = probes.build_status(
            _probes(broker_up=False),
            _units(PAST_DWELL),
            "t",
            units_age_secs=0,
            now_et=TUESDAY_1000_ET,
        )
        assert body["overall_state"] == "down"
        assert "radon-ib-gateway.service" in body["degraded_reasons"]
        assert "ib-gateway" in body["degraded_reasons"]

    def test_no_broker_payload_past_dwell_is_not_green(self, monkeypatch):
        """No nested broker evidence at all is NOT a positive precondition —
        this was the edge-green-with-dead-gateway hole."""
        monkeypatch.setenv("RADON_HOST_ROLE", "app")
        body = probes.build_status(
            _probes(broker_up=False, with_payload=False),
            _units(PAST_DWELL),
            "t",
            units_age_secs=0,
            now_et=TUESDAY_1000_ET,
        )
        assert body["overall_state"] != "up"
        assert "radon-ib-gateway.service" in body["degraded_reasons"]

    def test_broker_down_within_dwell_stays_suppressed(self, monkeypatch):
        """Inside the 900s dwell the exclusion still holds (flap absorption);
        the broker failure itself is already counted as radon-api:broker."""
        monkeypatch.setenv("RADON_HOST_ROLE", "app")
        body = probes.build_status(
            _probes(broker_up=False),
            _units(WITHIN_DWELL),
            "t",
            units_age_secs=0,
            now_et=TUESDAY_1000_ET,
        )
        assert body["overall_state"] == "degraded"
        assert body["degraded_reasons"] == ["radon-api:broker"]
        assert body["not_applicable"] == ["ib-gateway", "radon-ib-gateway.service"]

    def test_true_app_host_broker_up_stays_not_applicable(self, monkeypatch):
        """Positive precondition holds: nested broker probe up, so a locally
        absent gateway stays structurally inapplicable indefinitely."""
        monkeypatch.setenv("RADON_HOST_ROLE", "app")
        body = probes.build_status(
            _probes(broker_up=True),
            _units(40 * 3600.0, result="success"),
            "t",
            units_age_secs=0,
            now_et=TUESDAY_1000_ET,
        )
        assert body["overall_state"] == "up"
        assert body["degraded_reasons"] == []
        assert body["not_applicable"] == ["ib-gateway", "radon-ib-gateway.service"]

    def test_true_app_host_clean_absent_gateway_stays_degraded_when_broker_is_down(
        self, monkeypatch,
    ):
        """2026-09-15 23:50Z page 3a6de316: app host, serving path up,
        /edge-health/ping 200, /sign-in 200, local radon-ib-gateway.service
        UnitFileState=disabled Result=success inactive since 2026-08-30,
        nested /health/lite auth_state=unreachable port_listening=false.

        Expiring the role exclusion because the remote broker is down
        re-counts the structurally absent unit. At 19:50 ET the EXT window
        is still open, so dwell then collapses the aggregate to down and
        the off-box observer pages P1 aggregate_down. Nested broker-down
        is already radon-api:broker (degraded); the local unit must stay
        not_applicable. Misapplied-role deaths stay exit-code and still
        escalate (test_broker_down_past_dwell_counts_the_gateway).
        """
        monkeypatch.setenv("RADON_HOST_ROLE", "app")
        live_probes = {
            "radon-api": {
                "state": "up",
                "http_status": 200,
                "payload": {
                    "status": "ok",
                    "auth_state": "unreachable",
                    "service_state": "unreachable",
                    "upstream_dead": False,
                    "port_listening": False,
                    "loop_lag_ms": 0.0,
                },
            },
            "radon-relay": {"state": "up"},
            "radon-nextjs": {"state": "up"},
            "ib-gateway": {"state": "down", "detail": "ConnectionRefusedError"},
            "radon-mcp": {"state": "up", "http_status": 406},
        }
        live_units = {
            "radon-api.service": {
                "active_state": "active",
                "sub_state": "running",
                "result": "success",
                "state": "up",
                "non_up_secs": None,
            },
            "radon-relay.service": {
                "active_state": "active",
                "sub_state": "running",
                "result": "success",
                "state": "up",
                "non_up_secs": None,
            },
            "radon-monitor.service": {
                "active_state": "active",
                "sub_state": "running",
                "result": "success",
                "state": "up",
                "non_up_secs": None,
            },
            "radon-nextjs.service": {
                "active_state": "active",
                "sub_state": "running",
                "result": "success",
                "state": "up",
                "non_up_secs": None,
            },
            "radon-ib-gateway.service": {
                "active_state": "inactive",
                "sub_state": "dead",
                "result": "success",
                "state": "down",
                "non_up_secs": 6048.3,
            },
            "radon-newsfeed.service": {
                "active_state": "active",
                "sub_state": "running",
                "result": "success",
                "state": "up",
                "non_up_secs": None,
            },
        }
        body = probes.build_status(
            live_probes,
            live_units,
            "2026-09-15T23:50:54.313391+00:00",
            "ok",
            units_age_secs=1.9,
            probes_age_secs=3.3,
            now_et=TUESDAY_1950_ET,
        )
        assert body["overall_state"] == "degraded"
        assert body["ok"] is False
        assert body["degraded_reasons"] == ["radon-api:broker"]
        assert body["not_applicable"] == ["ib-gateway", "radon-ib-gateway.service"]
        assert body["role_suppression_expired"] is False
        verdict = edge_probe.classify_probes(
            {"reachable": True, "http_status": 200},
            {"reachable": True, "http_status": 200, "payload": body},
        )
        assert verdict["ok"] == 1
        assert "aggregate_down" not in verdict["detail"]


class TestStatusAcknowledgement:
    def test_expiry_is_visible_in_status(self, monkeypatch):
        monkeypatch.setenv("RADON_HOST_ROLE", "app")
        body = probes.build_status(
            _probes(broker_up=False),
            _units(PAST_DWELL),
            "t",
            units_age_secs=0,
            now_et=TUESDAY_1000_ET,
        )
        assert body["not_applicable"] == []
        assert body["role_suppression_expired"] is True

    def test_active_suppression_is_not_flagged_expired(self, monkeypatch):
        monkeypatch.setenv("RADON_HOST_ROLE", "app")
        body = probes.build_status(
            _probes(broker_up=True),
            _units(40 * 3600.0, result="success"),
            "t",
            units_age_secs=0,
            now_et=TUESDAY_1000_ET,
        )
        assert body["role_suppression_expired"] is False

    def test_non_app_roles_never_flag_expiry(self, monkeypatch):
        monkeypatch.setenv("RADON_HOST_ROLE", "broker")
        body = probes.build_status(
            _probes(broker_up=False),
            _units(PAST_DWELL),
            "t",
            units_age_secs=0,
            now_et=TUESDAY_1000_ET,
        )
        assert body["role_suppression_expired"] is False
