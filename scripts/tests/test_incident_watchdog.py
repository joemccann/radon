"""Incident watchdog — pure core tests (classify, parse, store).

The probe layer is thin stdlib I/O; everything judgeable is a pure function
so the failure-mode classification and the incident JSON lifecycle are fully
testable with pinned clocks and synthetic bodies.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from incident_watchdog.classify import classify
from incident_watchdog.probes import (
    parse_freshness_body,
    parse_health_status_body,
    parse_service_health_body,
)
from incident_watchdog.store import record_cycle

NOW = datetime(2026, 8, 3, 17, 0, 0, tzinfo=timezone.utc)


def healthy_findings():
    return {
        "nextjs_liveness": {"state": "up", "http_status": 200, "detail": ""},
        "nextjs_db": {
            "state": "up",
            "synthetic_turso_row": False,
            "failing": [],
            "warning": None,
        },
        "api_lite": {"state": "up", "loop_lag_ms": 12.0},
        "health_status": {"state": "up", "ok": True, "overall_state": "healthy"},
        "freshness": {
            "state": "up",
            "all_fresh": True,
            "stale_checks": [],
            "database_ok": True,
        },
        "deploy": {
            "state": "up",
            "ci": {"status": "completed", "conclusion": "success", "head_sha": "abc"},
            "green_marker": "abc",
            "head": "abc",
        },
    }


class TestClassify:
    def test_healthy_cycle_yields_no_incidents(self):
        assert classify(healthy_findings(), NOW) == []

    def test_synthetic_turso_row_is_p1_destroy_storm_candidate(self):
        findings = healthy_findings()
        findings["nextjs_db"] = {
            "state": "up",
            "synthetic_turso_row": True,
            "failing": [{"service": "turso-db", "state": "error"}],
            "warning": "service_health read timed out after 3000ms",
        }
        incidents = classify(findings, NOW)
        assert len(incidents) == 1
        inc = incidents[0]
        assert inc["case_id"] == "turso-destroy-storm"
        assert inc["severity"] == "P1"
        assert inc["fingerprint"] == "turso-destroy-storm"
        # The runbook's discriminating counter-signal must travel with the
        # incident so the responder runs the Python canary before restarting.
        assert "canary" in json.dumps(inc["evidence"]).lower()

    def test_public_route_500_is_p1_corrupt_build(self):
        findings = healthy_findings()
        findings["nextjs_liveness"] = {
            "state": "down",
            "http_status": 500,
            "detail": "/sign-in returned 500",
        }
        findings["deploy"]["ci"] = {
            "status": "completed",
            "conclusion": "cancelled",
            "head_sha": "abc",
        }
        incidents = classify(findings, NOW)
        assert [i["case_id"] for i in incidents] == [
            "cancelled-deploy-corrupt-next-build"
        ]
        assert incidents[0]["severity"] == "P1"

    def test_green_marker_mismatch_after_completed_ci_is_p2(self):
        findings = healthy_findings()
        findings["deploy"] = {
            "state": "up",
            "ci": {"status": "completed", "conclusion": "success", "head_sha": "def"},
            "green_marker": "abc",
            "head": "def",
        }
        incidents = classify(findings, NOW)
        assert [i["case_id"] for i in incidents] == [
            "cancelled-deploy-corrupt-next-build"
        ]
        assert incidents[0]["severity"] == "P2"

    def test_marker_mismatch_during_inflight_deploy_is_not_an_incident(self):
        findings = healthy_findings()
        findings["deploy"] = {
            "state": "up",
            "ci": {"status": "in_progress", "conclusion": None, "head_sha": "def"},
            "green_marker": "abc",
            "head": "def",
        }
        assert classify(findings, NOW) == []

    def test_stale_freshness_is_p2(self):
        findings = healthy_findings()
        findings["freshness"] = {
            "state": "up",
            "all_fresh": False,
            "stale_checks": ["relay_tick", "vcg_scan"],
            "database_ok": True,
        }
        incidents = classify(findings, NOW)
        assert [i["case_id"] for i in incidents] == ["stale-market-data-freshness"]
        assert incidents[0]["severity"] == "P2"
        assert "relay_tick" in incidents[0]["fingerprint"]

    def test_freshness_not_applicable_off_hours_is_quiet(self):
        findings = healthy_findings()
        findings["freshness"] = {
            "state": "up",
            "all_fresh": None,
            "stale_checks": [],
            "database_ok": True,
        }
        assert classify(findings, NOW) == []

    def test_unknown_probe_states_never_classify(self):
        findings = healthy_findings()
        for name in findings:
            findings[name] = {"state": "unknown"}
        assert classify(findings, NOW) == []

    def test_degraded_service_rows_are_p2_with_stable_fingerprint(self):
        findings = healthy_findings()
        findings["nextjs_db"]["failing"] = [
            {"service": "vcg-scan", "state": "stale"},
            {"service": "cri-scan", "state": "error"},
        ]
        incidents = classify(findings, NOW)
        assert [i["case_id"] for i in incidents] == ["service-health-degraded"]
        assert incidents[0]["severity"] == "P2"
        assert incidents[0]["fingerprint"] == (
            "service-health-degraded:cri-scan,vcg-scan"
        )

    def test_fastapi_down_is_p1_service_down(self):
        findings = healthy_findings()
        findings["api_lite"] = {"state": "down", "loop_lag_ms": None}
        incidents = classify(findings, NOW)
        assert [i["case_id"] for i in incidents] == ["service-down"]
        assert incidents[0]["severity"] == "P1"
        assert "fastapi" in incidents[0]["fingerprint"]


class TestParsers:
    def test_parse_service_health_detects_synthetic_row(self):
        body = {
            "services": [
                {"service": "turso-db", "state": "error"},
            ],
            "failing": [{"service": "turso-db", "state": "error"}],
            "warning": "read timed out after 3000ms",
        }
        parsed = parse_service_health_body(body)
        assert parsed["synthetic_turso_row"] is True
        assert parsed["warning"] == "read timed out after 3000ms"

    def test_parse_service_health_normal_body(self):
        body = {
            "services": [{"service": "vcg-scan", "state": "ok"}],
            "failing": [{"service": "cri-scan", "state": "stale", "x": 1}],
        }
        parsed = parse_service_health_body(body)
        assert parsed["synthetic_turso_row"] is False
        assert parsed["failing"] == [{"service": "cri-scan", "state": "stale"}]

    def test_parse_freshness_extracts_stale_applicable_checks(self):
        body = {
            "all_fresh": False,
            "database_ok": True,
            "checks": {
                "relay_tick": {"applicable": True, "fresh": False, "age_secs": 195},
                "vcg_scan": {"applicable": True, "fresh": True, "age_secs": 60},
                "journal": {"applicable": False, "fresh": None, "age_secs": None},
            },
        }
        parsed = parse_freshness_body(body)
        assert parsed["all_fresh"] is False
        assert parsed["stale_checks"] == ["relay_tick"]

    def test_parse_health_status_body(self):
        parsed = parse_health_status_body({"ok": False, "overall_state": "degraded"})
        assert parsed["ok"] is False
        assert parsed["overall_state"] == "degraded"


class TestStore:
    def incident(self, fingerprint="turso-destroy-storm"):
        return {
            "case_id": "turso-destroy-storm",
            "severity": "P1",
            "title": "Next.js Turso reads failing while process is up",
            "fingerprint": fingerprint,
            "evidence": {"warning": "read timed out"},
        }

    def test_new_incident_writes_open_file_with_schema(self, tmp_path: Path):
        result = record_cycle([self.incident()], tmp_path, NOW)
        assert len(result["opened"]) == 1
        payload = json.loads(Path(result["opened"][0]).read_text())
        assert payload["schema"] == "radon.incident/1"
        assert payload["status"] == "open"
        assert payload["case_id"] == "turso-destroy-storm"
        assert payload["detected_at"] == NOW.isoformat()
        assert payload["observations"] == 1
        assert payload["runbook"].startswith("docs/incident-runbook.md#")

    def test_repeat_detection_updates_instead_of_duplicating(self, tmp_path: Path):
        first = record_cycle([self.incident()], tmp_path, NOW)
        later = NOW.replace(minute=5)
        second = record_cycle([self.incident()], tmp_path, later)
        assert second["opened"] == []
        assert len(second["updated"]) == 1
        payload = json.loads(Path(first["opened"][0]).read_text())
        assert payload["observations"] == 2
        assert payload["last_seen_at"] == later.isoformat()

    def test_clean_cycle_resolves_open_incidents(self, tmp_path: Path):
        opened = record_cycle([self.incident()], tmp_path, NOW)
        later = NOW.replace(minute=10)
        result = record_cycle([], tmp_path, later)
        assert len(result["resolved"]) == 1
        payload = json.loads(Path(opened["opened"][0]).read_text())
        assert payload["status"] == "resolved"
        assert payload["resolved_at"] == later.isoformat()

    def test_redetection_after_resolve_opens_a_new_file(self, tmp_path: Path):
        record_cycle([self.incident()], tmp_path, NOW)
        record_cycle([], tmp_path, NOW.replace(minute=10))
        result = record_cycle([self.incident()], tmp_path, NOW.replace(minute=20))
        assert len(result["opened"]) == 1
        assert len(list(tmp_path.glob("incident-*.json"))) == 2
