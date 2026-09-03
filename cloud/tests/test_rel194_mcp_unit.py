"""REL-194 (R-553, R-554): the MCP unit holds only what it needs, is
hardened, and its liveness is watched."""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
UNIT = REPO / "cloud" / "services" / "radon-mcp.service"
HEALTH_UNIT = REPO / "cloud" / "services" / "radon-health.service"
SCRIPTS = REPO / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def _uncommented(path: Path) -> str:
    return "\n".join(
        line for line in path.read_text().splitlines()
        if not line.lstrip().startswith("#")
    )


class TestUnitEnvironmentIsAllowlisted:
    def test_the_exec_env_is_an_allowlist_not_the_whole_secret_file(self):
        """R-553: the internet-facing process inherited UW_TOKEN,
        IB_FLEX_TOKEN, Turso and B2 creds from /etc/radon/env."""
        src = _uncommented(UNIT)
        exec_line = next(
            line for line in src.splitlines() if line.startswith("ExecStart=")
        )
        assert "env -i" in exec_line, (
            "ExecStart does not scrub the inherited environment"
        )
        for needed in ("CLERK_JWKS_URL", "CLERK_ISSUER", "ALLOWED_USER_IDS"):
            assert needed in exec_line
        for secret in ("UW_TOKEN", "IB_FLEX_TOKEN", "TURSO", "CLERK_SECRET_KEY"):
            assert secret not in exec_line

    def test_the_standard_hardening_set_is_present(self):
        src = _uncommented(UNIT)
        for directive in (
            "NoNewPrivileges=yes",
            "ProtectSystem=strict",
            "ProtectHome=read-only",
            "PrivateTmp=yes",
        ):
            assert directive in src, f"missing {directive}"


class TestLivenessProbe:
    def test_health_daemon_probes_mcp_when_configured(self, monkeypatch):
        from health_service import serve

        monkeypatch.setenv("RADON_MCP_PROBE_URL", "http://127.0.0.1:1/mcp")
        results = serve.run_probes()
        assert "radon-mcp" in results

    def test_mcp_probe_absent_when_unconfigured(self, monkeypatch):
        from health_service import serve

        monkeypatch.delenv("RADON_MCP_PROBE_URL", raising=False)
        results = serve.run_probes()
        assert "radon-mcp" not in results

    def test_mcp_is_a_dependency_probe_not_a_serving_path(self):
        from health_service import probes

        assert "radon-mcp" in probes.DEPENDENCY_PROBES

    def test_an_http_error_status_is_still_alive(self, monkeypatch):
        """FastMCP answers GET /mcp with 405/406 — liveness, not death."""
        import urllib.error
        from health_service import probes

        def _raise(*a, **k):
            raise urllib.error.HTTPError("u", 405, "nope", {}, None)

        monkeypatch.setattr(probes.urllib.request, "urlopen", _raise)
        result = probes.probe_http_alive("http://127.0.0.1:8334/mcp")
        assert result["state"] == "up"

    def test_a_wedged_listener_times_out_to_unknown(self, monkeypatch):
        import socket
        from health_service import probes

        def _raise(*a, **k):
            raise socket.timeout()

        monkeypatch.setattr(probes.urllib.request, "urlopen", _raise)
        result = probes.probe_http_alive("http://127.0.0.1:8334/mcp")
        assert result["state"] == "unknown"

    def test_the_health_unit_enables_the_probe(self):
        src = _uncommented(HEALTH_UNIT)
        assert "RADON_MCP_PROBE_URL" in src
