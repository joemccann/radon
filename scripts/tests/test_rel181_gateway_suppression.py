"""REL-181 (R-478, R-510): the IB-gateway dwell suppression is calendar-scoped
and result-scoped, and a degraded aggregate names its reasons.

R-478 (NF-10): 8a0b95ce excluded `radon-ib-gateway.service` from
`DWELL_ESCALATE_UNITS` outright — no calendar, no dwell bound, no
`Result=success` discriminator — so a gateway dead at Tuesday 10:00 ET with
`Result=exit-code` could never escalate the edge floor past `degraded`.
The runbook's rule ("market closed + Result=success") existed only in prose.
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
from health_probe import probe as edge_probe  # noqa: E402

ET = ZoneInfo("America/New_York")

UP_PROBES = {
    "radon-api": {"state": "up"},
    "radon-relay": {"state": "up"},
    "radon-nextjs": {"state": "up"},
}


def _units_with_gateway(result: str, dwell: float = 3600.0) -> dict:
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
            "non_up_secs": dwell,
        },
    }


TUESDAY_1000_ET = datetime(2026, 9, 8, 10, 0, tzinfo=ET)
SATURDAY_ET = datetime(2026, 9, 5, 22, 0, tzinfo=ET)
TUESDAY_2100_ET = datetime(2026, 9, 8, 21, 0, tzinfo=ET)

# T-460: every verdict here pins host_role explicitly. With the parameter
# omitted, probes.resolve_host_role() reads RADON_HOST_ROLE from the ambient
# environment, so these suites only held on hosts where scripts/conftest.py's
# env scrub ran — a split-role host exporting `app` flipped them.
COMBINED = "combined"


class TestGatewayDwellPredicate:
    def test_exit_code_death_in_rth_escalates(self):
        state = probes.aggregate_state(
            UP_PROBES, _units_with_gateway("exit-code"), "ok", 2.0, host_role=COMBINED,
            now_et=TUESDAY_1000_ET,
        )
        assert state == "down"

    def test_clean_exit_in_rth_still_escalates(self):
        """Result=success during a Tuesday session is not the weekend shape —
        a gateway that will not come back during trading pages."""
        state = probes.aggregate_state(
            UP_PROBES, _units_with_gateway("success"), "ok", 2.0, host_role=COMBINED,
            now_et=TUESDAY_1000_ET,
        )
        assert state == "down"

    def test_weekend_clean_exit_stays_degraded(self):
        state = probes.aggregate_state(
            UP_PROBES, _units_with_gateway("success", dwell=40 * 3600.0), "ok", 2.0, host_role=COMBINED,
            now_et=SATURDAY_ET,
        )
        assert state == "degraded"

    def test_weekday_overnight_clean_exit_stays_degraded(self):
        """IBKR nightly session shutdown: dead Result=success at 21:00 ET."""
        state = probes.aggregate_state(
            UP_PROBES, _units_with_gateway("success"), "ok", 2.0, host_role=COMBINED,
            now_et=TUESDAY_2100_ET,
        )
        assert state == "degraded"

    def test_weekend_exit_code_death_escalates(self):
        """The calendar alone is not enough — a crash is a crash on Saturday."""
        state = probes.aggregate_state(
            UP_PROBES, _units_with_gateway("exit-code", dwell=3600.0), "ok", 2.0, host_role=COMBINED,
            now_et=SATURDAY_ET,
        )
        assert state == "down"


class TestAppRoleDropOut:
    """T-460: the app-role drop-out path (2026-08-30 two-host split).

    On an `app` host the local gateway probe/unit are absent by design; while
    the nested `radon-api:broker` probe is up (the broker is covered from the
    broker host), both drop out of the aggregate instead of paging. This path
    was invisible while these suites relied on the conftest env scrub."""

    APP_PROBES = {
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

    def test_an_absent_gateway_drops_out_while_the_broker_probe_is_up(self):
        # Identical snapshot to test_exit_code_death_in_rth_escalates, where
        # the combined role reads "down" — only the role differs.
        state = probes.aggregate_state(
            self.APP_PROBES, _units_with_gateway("exit-code"), "ok", 2.0,
            now_et=TUESDAY_1000_ET, host_role="app",
        )
        assert state == "up"

    def test_build_status_labels_the_drop_out(self):
        payload = probes.build_status(
            self.APP_PROBES, _units_with_gateway("exit-code"),
            "2026-09-08T14:00:00Z", "ok", 2.0,
            now_et=TUESDAY_1000_ET, host_role="app",
        )
        assert payload["overall_state"] == "up"
        assert "radon-ib-gateway.service" in payload["not_applicable"]
        assert "ib-gateway" in payload["not_applicable"]
        assert "radon-ib-gateway.service" not in payload["degraded_reasons"]


class TestDegradedCarriesReasons:
    def test_build_status_names_the_dead_dependency(self):
        payload = probes.build_status(
            UP_PROBES,
            _units_with_gateway("success", dwell=40 * 3600.0),
            "2026-09-05T22:00:00Z",
            "ok",
            2.0,
            now_et=SATURDAY_ET,
            host_role=COMBINED,
        )
        assert payload["overall_state"] == "degraded"
        assert "radon-ib-gateway.service" in payload.get("degraded_reasons", [])

    def test_the_edge_verdict_carries_the_reason(self):
        payload = probes.build_status(
            UP_PROBES,
            _units_with_gateway("success", dwell=40 * 3600.0),
            "2026-09-05T22:00:00Z",
            "ok",
            2.0,
            now_et=SATURDAY_ET,
            host_role=COMBINED,
        )
        verdict = edge_probe.classify_probes(
            {"reachable": True, "http_status": 200},
            {"reachable": True, "http_status": 200, "payload": payload},
        )
        assert verdict["ok"] == 1
        assert verdict["detail"].startswith("edge_ok:aggregate_degraded")
        assert "radon-ib-gateway.service" in verdict["detail"]

    def test_a_healthy_status_has_no_reasons(self):
        units = _units_with_gateway("success")
        units["radon-ib-gateway.service"] = {"state": "up", "non_up_secs": None}
        payload = probes.build_status(
            UP_PROBES, units, "2026-09-08T14:00:00Z", "ok", 2.0, host_role=COMBINED,
            now_et=TUESDAY_1000_ET,
        )
        assert payload["overall_state"] == "up"
        assert payload.get("degraded_reasons") == []
