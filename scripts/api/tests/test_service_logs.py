"""GET /admin/logs: the journal behind a failed or stale /admin row.

Pins unit resolution (service_health name -> owning unit, timer -> its
service), the three fetch paths (host control socket, local systemd,
unavailable), parameter validation and auth parity with /admin/services.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

SCRIPTS_DIR = Path(__file__).resolve().parents[2]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from scripts.api import server  # noqa: E402

# server.py imports `api.*`, which can be a different module object from
# `scripts.api.*`; patch the exact objects the route uses.
service_logs = server.service_logs
host_control = service_logs.host_control
admin_services = service_logs.admin_services

JOURNAL_ARGV_TAIL = [
    "-n", "300", "--no-pager", "-q", "-o", "json", "--output-fields=MESSAGE,PRIORITY",
]


def _jline(us: int, message: str, priority: str = "6") -> str:
    return json.dumps({"__REALTIME_TIMESTAMP": str(us), "PRIORITY": priority, "MESSAGE": message})


@pytest.fixture
def overrides(monkeypatch, tmp_path):
    path = tmp_path / "service_log_units.json"
    path.write_text(json.dumps({
        "ib-realtime-relay": "radon-relay.service",
        "cri": "radon-cri-scan.service",
        "portfolio-browser-snapshot": None,
    }))
    monkeypatch.setattr(service_logs, "OVERRIDES_PATH", path)
    return path


@pytest.fixture
def no_journal(monkeypatch):
    monkeypatch.setattr(host_control, "socket_present", lambda: False)
    monkeypatch.setattr(admin_services, "is_systemd_available", lambda: False)


class _FakeHostControl:
    def __init__(self, reply):
        self.reply = reply
        self.requests: list[tuple[dict, float]] = []

    async def __call__(self, request, timeout):
        self.requests.append((dict(request), timeout))
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


class TestResolution:
    def test_default_is_radon_name_service(self, overrides):
        assert service_logs.resolve_unit(service="cor") == "radon-cor.service"

    def test_override_wins(self, overrides):
        assert service_logs.resolve_unit(service="ib-realtime-relay") == "radon-relay.service"
        assert service_logs.resolve_unit(service="cri") == "radon-cri-scan.service"

    def test_null_override_means_no_owning_unit(self, overrides):
        assert service_logs.resolve_unit(service="portfolio-browser-snapshot") is None

    def test_name_that_cannot_be_a_unit_resolves_to_none(self, overrides):
        assert service_logs.resolve_unit(service="Some_Writer") is None

    def test_timer_maps_to_its_service(self, overrides):
        assert service_logs.resolve_unit(unit="radon-cor.timer") == "radon-cor.service"
        assert service_logs.resolve_unit(unit="radon-api.service") == "radon-api.service"

    def test_missing_or_corrupt_overrides_file_falls_back_to_default(self, monkeypatch, tmp_path):
        monkeypatch.setattr(service_logs, "OVERRIDES_PATH", tmp_path / "absent.json")
        assert service_logs.resolve_unit(service="cor") == "radon-cor.service"
        bad = tmp_path / "bad.json"
        bad.write_text("{not json")
        monkeypatch.setattr(service_logs, "OVERRIDES_PATH", bad)
        assert service_logs.resolve_unit(service="cor") == "radon-cor.service"

    def test_invalid_override_value_is_not_trusted(self, monkeypatch, tmp_path):
        path = tmp_path / "o.json"
        path.write_text(json.dumps({"x": "ssh.service", "y": "radon-beta-api.service"}))
        monkeypatch.setattr(service_logs, "OVERRIDES_PATH", path)
        assert service_logs.resolve_unit(service="x") is None
        assert service_logs.resolve_unit(service="y") is None

    def test_checked_in_overrides_file_is_valid(self):
        data = json.loads(service_logs.DEFAULT_OVERRIDES_PATH.read_text())
        assert isinstance(data, dict)
        for name, unit in data.items():
            assert isinstance(name, str) and name
            assert unit is None or service_logs.LOG_UNIT_RE.match(unit), (name, unit)


@pytest.fixture
def client(monkeypatch):
    from scripts.api import auth, server

    monkeypatch.setattr(auth, "is_trusted_local_request", lambda request: True)
    monkeypatch.setattr(server, "is_trusted_local_request", lambda request: True)
    return TestClient(server.app)


class TestParams:
    @pytest.mark.parametrize("query", [
        "",
        "?service=cor&unit=radon-cor.service",
        "?service=",
        "?unit=",
        "?unit=ssh.service",
        "?unit=radon-api",
        "?unit=radon-api.socket",
        "?unit=radon-beta-api.service",
        "?unit=radon-ib-gateway-preheld-restart.service",
        "?unit=-n%209999",
        "?service=cor%3Bid",
        "?service=" + "a" * 200,
    ])
    def test_bad_params_are_400(self, client, overrides, no_journal, query):
        resp = client.get("/admin/logs" + query)
        assert resp.status_code == 400, resp.text


class TestHostControlPath:
    def test_service_logs_go_through_the_socket(self, client, overrides, monkeypatch):
        monkeypatch.setattr(host_control, "socket_present", lambda: True)
        monkeypatch.setattr(admin_services, "is_systemd_available", lambda: True)
        fake = _FakeHostControl({
            "ok": True, "unit": "radon-relay.service", "truncated": True,
            "entries": [
                {"ts": "2026-10-12T10:00:00.000000Z", "priority": 3, "message": "relay stalled"},
                {"ts": "2026-10-12T10:00:01.000000Z", "priority": None, "message": "retrying"},
                "garbage",
            ],
        })
        monkeypatch.setattr(host_control, "acall", fake)
        resp = client.get("/admin/logs?service=ib-realtime-relay")
        assert resp.status_code == 200, resp.text
        assert fake.requests[0][0] == {"op": "logs", "unit": "radon-relay.service", "actor": "local"}
        body = resp.json()
        assert body == {
            "service": "ib-realtime-relay",
            "unit": "radon-relay.service",
            "available": True,
            "reason": None,
            "entries": [
                {"ts": "2026-10-12T10:00:00.000000Z", "priority": 3, "message": "relay stalled"},
                {"ts": "2026-10-12T10:00:01.000000Z", "priority": None, "message": "retrying"},
            ],
            "truncated": True,
        }

    def test_timer_unit_reads_its_service_journal(self, client, overrides, monkeypatch):
        monkeypatch.setattr(host_control, "socket_present", lambda: True)
        fake = _FakeHostControl({"ok": True, "unit": "radon-cor.service", "entries": [], "truncated": False})
        monkeypatch.setattr(host_control, "acall", fake)
        resp = client.get("/admin/logs?unit=radon-cor.timer")
        assert resp.status_code == 200
        assert fake.requests[0][0]["unit"] == "radon-cor.service"
        body = resp.json()
        assert body["service"] is None and body["unit"] == "radon-cor.service"
        assert body["available"] is True and body["entries"] == []

    def test_daemon_refusal_is_unavailable_not_an_error(self, client, overrides, monkeypatch):
        monkeypatch.setattr(host_control, "socket_present", lambda: True)
        fake = _FakeHostControl({"ok": False, "refused": True,
                                 "error": "radon-cor.service is not a loaded radon unit"})
        monkeypatch.setattr(host_control, "acall", fake)
        resp = client.get("/admin/logs?service=cor")
        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is False
        assert "not a loaded radon unit" in body["reason"]
        assert body["entries"] == []

    def test_unreachable_daemon_is_unavailable_not_500(self, client, overrides, monkeypatch):
        monkeypatch.setattr(host_control, "socket_present", lambda: True)
        monkeypatch.setattr(
            host_control, "acall",
            _FakeHostControl(host_control.HostControlError("host control timed out after 20s")),
        )
        resp = client.get("/admin/logs?service=cor")
        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is False and "timed out" in body["reason"]


class TestSystemdPath:
    def test_runs_the_fixed_journalctl_argv_without_sudo(self, client, overrides, monkeypatch):
        monkeypatch.setattr(host_control, "socket_present", lambda: False)
        monkeypatch.setattr(admin_services, "is_systemd_available", lambda: True)
        calls = []

        async def run(argv, timeout):
            calls.append((list(argv), timeout))
            return 0, "\n".join([
                _jline(1_000_000, "started"),
                "not json",
                _jline(2_000_000, "auth failed token=abc123secret", "3"),
            ]), ""

        monkeypatch.setattr(service_logs, "_run_journalctl", run)
        resp = client.get("/admin/logs?service=cor")
        assert resp.status_code == 200, resp.text
        assert calls == [(["journalctl", "-u", "radon-cor.service", *JOURNAL_ARGV_TAIL],
                          service_logs.JOURNAL_TIMEOUT_S)]
        body = resp.json()
        assert body["available"] is True and body["unit"] == "radon-cor.service"
        assert [e["message"] for e in body["entries"]] == ["started", "auth failed token=[REDACTED]"]
        assert body["entries"][1]["priority"] == 3
        assert body["truncated"] is False

    def test_journalctl_failure_is_unavailable(self, client, overrides, monkeypatch):
        monkeypatch.setattr(host_control, "socket_present", lambda: False)
        monkeypatch.setattr(admin_services, "is_systemd_available", lambda: True)

        async def run(argv, timeout):
            return 1, "", "Failed to get journal access: Permission denied"

        monkeypatch.setattr(service_logs, "_run_journalctl", run)
        body = client.get("/admin/logs?unit=radon-api.service").json()
        assert body["available"] is False
        assert "Permission denied" in body["reason"]


class TestUnavailable:
    def test_no_socket_no_systemd_is_200_unavailable(self, client, overrides, no_journal):
        resp = client.get("/admin/logs?service=cor")
        assert resp.status_code == 200
        body = resp.json()
        assert body == {
            "service": "cor",
            "unit": "radon-cor.service",
            "available": False,
            "reason": body["reason"],
            "entries": [],
            "truncated": False,
        }
        assert body["reason"] and "journal" in body["reason"].lower()

    def test_service_without_owning_unit_is_200_unavailable(self, client, overrides, monkeypatch):
        monkeypatch.setattr(host_control, "socket_present", lambda: True)
        fake = _FakeHostControl({"ok": True, "entries": []})
        monkeypatch.setattr(host_control, "acall", fake)
        resp = client.get("/admin/logs?service=portfolio-browser-snapshot")
        assert resp.status_code == 200
        body = resp.json()
        assert body["unit"] is None and body["available"] is False
        assert "portfolio-browser-snapshot" in body["reason"]
        assert fake.requests == []


class TestAuthParity:
    @pytest.fixture
    def untrusted(self, monkeypatch):
        from scripts.api import auth, server

        monkeypatch.setenv("CLERK_JWKS_URL", "https://example.test/.well-known/jwks.json")
        monkeypatch.delenv("RADON_AUTH_DISABLED", raising=False)
        monkeypatch.setattr(auth, "is_trusted_local_request", lambda request: False)
        monkeypatch.setattr(server, "is_trusted_local_request", lambda request: False)
        monkeypatch.setattr(server, "verify_api_key", lambda request: None)

        async def deny(request):
            raise HTTPException(status_code=401, detail="Invalid token")

        monkeypatch.setattr(server, "verify_clerk_jwt", deny)
        monkeypatch.setattr(auth, "verify_clerk_jwt", deny)
        return TestClient(server.app)

    def test_anonymous_caller_is_denied_like_admin_services(self, untrusted, overrides, no_journal):
        services = untrusted.get("/admin/services")
        logs = untrusted.get("/admin/logs?service=cor")
        assert services.status_code in {401, 403}
        assert logs.status_code == services.status_code

    def test_classified_admin_for_the_assistant(self):
        from scripts.api.assistant_catalog import CATALOG

        assert CATALOG[("GET", "/admin/logs")] == CATALOG[("GET", "/admin/services")] == "admin"
