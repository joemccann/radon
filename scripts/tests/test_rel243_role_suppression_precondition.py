"""REL-243 (R-650, NF-10): the app-role gateway suppression needs a positive
precondition or a dwell bound.

`ROLE_NOT_APPLICABLE` dropped `ib-gateway` / `radon-ib-gateway.service` from
the aggregate unconditionally on `RADON_HOST_ROLE=app`. Copy that env onto a
host that IS running a local gateway and the :4001 probe never enters the
aggregate again: edge-green with a dead gateway, forever. The exclusion now
holds only while the nested `radon-api:broker` probe is observed up; otherwise
it degrades to counted after the existing 900s dependency dwell
(`DEPENDENCY_DWELL_LIMIT_SECS`), and /status acknowledges the expiry.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

SCRIPTS = Path(__file__).resolve().parent.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from health_service import probes  # noqa: E402

ET = ZoneInfo("America/New_York")
TUESDAY_1000_ET = datetime(2026, 9, 8, 10, 0, tzinfo=ET)

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
