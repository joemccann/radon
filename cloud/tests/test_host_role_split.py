"""Phase 0 / 0B / 1-prep: combined-host-safe SPOF split contracts.

Default topology stays one VM. These tests pin the code that must land
before any second Hetzner CX exists: parameterized Tailscale bind, stray
indicator units on auto-sync, privileged control-plane refresh in sudoers,
no Gateway PartOf cascade, and RADON_HOST_ROLE gating.
"""
from __future__ import annotations

import hashlib
import importlib.util
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

CLOUD = Path(__file__).resolve().parents[1]
HELPER = CLOUD / "scripts" / "deploy-root-helper.sh"
DEPLOY = CLOUD / "scripts" / "deploy.sh"
CHECK_ENV = CLOUD / "scripts" / "check-env.py"
OPERATOR = CLOUD / "scripts" / "operator-radon.sh"
SUDOERS = CLOUD / "config" / "sudoers.d" / "radon-deploy"
ALLOWLIST = CLOUD / "config" / "auto-sync-units.txt"
REQUIRED_ENV = CLOUD / "config" / "required-env.txt"
COMPOSE = CLOUD / "docker-compose.yml"
RELAY = CLOUD / "services" / "radon-relay.service"
MONITOR = CLOUD / "services" / "radon-monitor.service"
GATEWAY = CLOUD / "services" / "radon-ib-gateway.service"


def _load_check_env():
    spec = importlib.util.spec_from_file_location("radon_check_env", CHECK_ENV)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _function_body(script: str, name: str) -> str:
    match = re.search(
        rf"^{name}\(\)\s*\{{",
        script,
        re.MULTILINE,
    )
    assert match, f"{name}() missing"
    start = match.end()
    depth = 1
    i = start
    while i < len(script) and depth:
        if script[i] == "{":
            depth += 1
        elif script[i] == "}":
            depth -= 1
        i += 1
    return script[start : i - 1]


def _valid_assignments(*, host: str = "127.0.0.1", role: str | None = None) -> dict[str, str]:
    keys = [
        line.strip()
        for line in REQUIRED_ENV.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assignments = {key: f"value_{key}" for key in keys}
    assignments.update(
        {
            "IB_GATEWAY_MODE": "cloud",
            "IB_GATEWAY_HOST": host,
            "RADON_MODE": "hetzner",
            "NODE_ENV": "production",
            "TRADING_MODE": "live",
            "IB_GATEWAY_PORT": "4001",
        }
    )
    if role is not None:
        assignments["RADON_HOST_ROLE"] = role
    return assignments


def _write_env(tmp_path: Path, assignments: dict[str, str]) -> Path:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "".join(f"{key}={value}\n" for key, value in assignments.items()),
        encoding="utf-8",
    )
    env_file.chmod(0o600)
    return env_file


def _run_check_env(env_file: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CHECK_ENV), str(env_file), str(REQUIRED_ENV)],
        capture_output=True,
        text=True,
        timeout=30,
    )


class TestAutoSyncStrays:
    INDICATORS = (
        "radon-hhlev.service",
        "radon-hhlev.timer",
        "radon-hyad.service",
        "radon-hyad.timer",
        "radon-vixts.service",
        "radon-vixts.timer",
    )
    STILL_EXCLUDED = (
        "radon-llm-index.service",
        "radon-llm-index.timer",
        "radon-mktnews.service",
        "radon-ib-gateway.service",
        "radon-db-backup.service",
        "radon-drift-audit.service",
        "radon-refresh.service",
        "radon-portfolio-sync.service",
    )

    def _names(self) -> list[str]:
        names = []
        for line in ALLOWLIST.read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if line:
                names.append(line)
        return names

    def test_indicator_strays_are_on_the_allowlist(self):
        names = set(self._names())
        missing = [name for name in self.INDICATORS if name not in names]
        assert missing == []

    def test_control_plane_and_gated_units_stay_off_the_allowlist(self):
        names = set(self._names())
        leaked = [name for name in self.STILL_EXCLUDED if name in names]
        assert leaked == []


class TestComposeTailscaleBind:
    def test_tailscale_bind_is_parameterized_with_the_live_default(self):
        compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
        ports = compose["services"]["ib-gateway"]["ports"]
        matching = [p for p in ports if "4001:4003" in str(p)]
        assert any(str(p).startswith("127.0.0.1:") for p in matching)
        interpolated = [str(p) for p in matching if "IB_GATEWAY_TAILSCALE_BIND" in str(p)]
        assert interpolated == [
            "${IB_GATEWAY_TAILSCALE_BIND:-100.112.32.16}:4001:4003"
        ]
        assert all("0.0.0.0" not in str(p) for p in ports)


class TestNoGatewayPartOf:
    def test_relay_and_monitor_are_not_part_of_gateway(self):
        for path in (RELAY, MONITOR):
            body = path.read_text(encoding="utf-8")
            assert "PartOf=radon-ib-gateway.service" not in body

    def test_gateway_start_does_not_want_relay_or_monitor(self):
        body = GATEWAY.read_text(encoding="utf-8")
        assert "Wants=radon-relay.service" not in body
        assert "Wants=radon-monitor.service" not in body
        assert "Wants=tailscaled.service" in body


class TestPrivilegedRefreshSudoers:
    def test_sudoers_grants_the_exact_privileged_verb(self):
        sudoers = SUDOERS.read_text(encoding="utf-8")
        assert (
            "/usr/local/sbin/radon-deploy-root refresh-control-plane-privileged"
            in sudoers
        )
        assert "radon-*" not in sudoers
        assert "*" not in sudoers.split("refresh-control-plane-privileged")[1].split(",")[0]

    def test_deploy_prefers_privileged_refresh_when_granted(self):
        body = _function_body(DEPLOY.read_text(encoding="utf-8"), "refresh_control_plane")
        assert "refresh-control-plane-privileged" in body
        privileged_at = body.index("refresh-control-plane-privileged")
        unit_at = body.index("refresh-control-plane")
        assert privileged_at < unit_at or body.count("refresh-control-plane") >= 2


class TestCheckEnvHostRole:
    def test_combined_still_requires_loopback(self, tmp_path: Path):
        env_file = _write_env(tmp_path, _valid_assignments())
        result = _run_check_env(env_file)
        assert result.returncode == 0, result.stdout + result.stderr

    def test_combined_still_rejects_magicdns(self, tmp_path: Path):
        env_file = _write_env(tmp_path, _valid_assignments(host="ib-gateway"))
        result = _run_check_env(env_file)
        assert result.returncode != 0
        assert "IB_GATEWAY_HOST" in result.stdout + result.stderr

    def test_app_role_accepts_rfc1918(self, tmp_path: Path):
        env_file = _write_env(
            tmp_path, _valid_assignments(host="10.0.0.4", role="app")
        )
        result = _run_check_env(env_file)
        assert result.returncode == 0, result.stdout + result.stderr

    def test_app_role_rejects_loopback_and_cgnat_and_public(self, tmp_path: Path):
        for host in ("127.0.0.1", "100.112.32.16", "8.8.8.8", "ib-gateway"):
            env_file = _write_env(tmp_path, _valid_assignments(host=host, role="app"))
            result = _run_check_env(env_file)
            assert result.returncode != 0, host
            assert "IB_GATEWAY_HOST" in result.stdout + result.stderr

    def test_broker_role_still_requires_loopback(self, tmp_path: Path):
        env_file = _write_env(
            tmp_path, _valid_assignments(host="10.0.0.4", role="broker")
        )
        result = _run_check_env(env_file)
        assert result.returncode != 0

    def test_unknown_role_fails_closed(self, tmp_path: Path):
        env_file = _write_env(tmp_path, _valid_assignments(role="k8s"))
        result = _run_check_env(env_file)
        assert result.returncode != 0
        assert "RADON_HOST_ROLE" in result.stdout + result.stderr

    def test_gateway_host_helper_matches_the_checker(self):
        mod = _load_check_env()
        assert mod._gateway_host_ok("combined", "127.0.0.1")
        assert not mod._gateway_host_ok("combined", "10.0.0.4")
        assert mod._gateway_host_ok("app", "10.0.0.4")
        assert mod._gateway_host_ok("app", "192.168.1.8")
        assert not mod._gateway_host_ok("app", "127.0.0.1")
        assert not mod._gateway_host_ok("app", "100.112.32.16")


class TestOperatorHostRole:
    def test_app_role_does_not_require_gateway(self):
        body = OPERATOR.read_text(encoding="utf-8")
        assert "read_host_role" in body
        app = body.split('app)', 1)[1].split("broker)", 1)[0]
        assert "radon-ib-gateway.service" not in app
        assert "radon-api.service" in app

    def test_broker_role_does_not_require_nextjs(self):
        body = OPERATOR.read_text(encoding="utf-8")
        broker = body.split("broker)", 1)[1].split("*)", 1)[0]
        assert "radon-ib-gateway.service" in broker
        assert "radon-nextjs.service" not in broker

    def test_broker_role_does_not_require_health(self):
        body = OPERATOR.read_text(encoding="utf-8")
        match = re.search(
            r"broker\)\s*REQUIRED_UNITS=\((.*?)\)\s*;;",
            body,
            re.DOTALL,
        )
        assert match, "broker REQUIRED_UNITS block missing"
        required = match.group(1)
        assert "radon-ib-gateway.service" in required
        assert "radon-ib-gateway-remote.service" in required
        assert "radon-health.service" not in required
        assert "radon-api.service" not in required

    def test_app_role_skips_gateway_control(self):
        body = OPERATOR.read_text(encoding="utf-8")
        assert 'HOST_ROLE" != "app"' in body or "HOST_ROLE != app" in body.replace(
            " ", ""
        )

    def test_app_role_copy_does_not_claim_gateway_cycle(self):
        body = OPERATOR.read_text(encoding="utf-8")
        assert "Gateway stays on the broker" in body
        assert "app-plane radon units" in body


class TestRemoteDaemonUnit:
    def test_binds_private_nic_only(self):
        unit = (CLOUD / "services" / "radon-ib-gateway-remote.service").read_text(
            encoding="utf-8"
        )
        assert "RADON_IB_REMOTE_BIND=10.0.0.4" in unit
        assert "RADON_IB_REMOTE_PORT=8340" in unit
        assert "RADON_IB_REMOTE_ALLOW=10.0.0.2" in unit
        assert "0.0.0.0" not in unit
        assert "python -m scripts.ib_gateway_remote.serve" in unit
        assert "\nRequires=" not in unit
        assert "\nPartOf=" not in unit
        assert "PartOf=radon-ib-gateway" not in unit

    def test_role_skip_covers_remote_unit(self):
        helper = (CLOUD / "scripts" / "deploy-root-helper.sh").read_text(encoding="utf-8")
        assert "services/radon-ib-gateway-remote.service" in helper
        assert "role_skips_control_plane_source" in helper


class TestDeployHostRole:
    def test_app_preflight_does_not_require_the_gateway_helper(self):
        deploy = DEPLOY.read_text(encoding="utf-8")
        assert "read_host_role" in deploy
        assert 'host_role" != "app"' in deploy
        assert "GATEWAY_CONTROL_HELPER" in deploy

    def test_preflight_warns_on_privileged_diffs_when_granted(self, tmp_path: Path):
        helper_source = "scripts/deploy-root-helper.sh"
        runner = tmp_path / "runner" / "cloud"
        rels = (
            "scripts/deploy.sh",
            helper_source,
            "scripts/ib-gateway-control.sh",
            "config/sudoers.d/radon-deploy",
            "services/radon-api.service",
        )
        for rel in rels:
            dest = runner / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(CLOUD / rel, dest)
        (runner / helper_source).write_text(
            (runner / helper_source).read_text(encoding="utf-8") + "\n# privileged-diff\n",
            encoding="utf-8",
        )

        def digest(payload: bytes) -> str:
            return hashlib.sha256(payload).hexdigest()

        originals = {
            helper_source: (CLOUD / helper_source).read_bytes(),
            "scripts/ib-gateway-control.sh": (
                CLOUD / "scripts" / "ib-gateway-control.sh"
            ).read_bytes(),
            "config/sudoers.d/radon-deploy": SUDOERS.read_bytes(),
            "services/radon-api.service": (
                CLOUD / "services" / "radon-api.service"
            ).read_bytes(),
        }
        root_helper = tmp_path / "radon-deploy-root"
        gateway_helper = tmp_path / "radon-ib-gateway-control"
        sudoers_target = tmp_path / "radon-deploy"
        api_target = tmp_path / "radon-api.service"
        root_helper.write_bytes(originals[helper_source])
        gateway_helper.write_bytes(originals["scripts/ib-gateway-control.sh"])
        sudoers_target.write_bytes(originals["config/sudoers.d/radon-deploy"])
        api_target.write_bytes(originals["services/radon-api.service"])
        root_helper.chmod(0o755)
        gateway_helper.chmod(0o755)
        manifest = tmp_path / "control-plane-manifest.sha256"
        manifest.write_text(
            f"{digest(originals[helper_source])}  {helper_source} -> {root_helper}\n"
            f"{digest(originals['scripts/ib-gateway-control.sh'])}  "
            f"scripts/ib-gateway-control.sh -> {gateway_helper}\n"
            f"{digest(originals['config/sudoers.d/radon-deploy'])}  "
            f"config/sudoers.d/radon-deploy -> {sudoers_target}\n"
            f"{digest(originals['services/radon-api.service'])}  "
            f"services/radon-api.service -> {api_target}\n",
            encoding="utf-8",
        )
        ready = tmp_path / "control-plane-ready"
        ready.write_text(f"{digest(manifest.read_bytes())}  {manifest}\n", encoding="utf-8")
        sha256sum = shutil.which("sha256sum")
        assert sha256sum is not None
        shell = f"""
set -euo pipefail
source {runner / "scripts" / "deploy.sh"}
sudo() {{
  if [[ "$*" == "-n -l -- {root_helper} refresh-control-plane-privileged" ]]; then
    return 0
  fi
  [[ "$*" == "-n {root_helper} verify-control-plane" \
    || "$*" == "-n {root_helper} verify-restored" ]]
}}
preflight_control_plane
"""
        result = subprocess.run(
            ["bash", "-c", shell],
            env={
                **os.environ,
                "RADON_DEPLOY_ROOT_HELPER": str(root_helper),
                "RADON_GATEWAY_CONTROL_HELPER": str(gateway_helper),
                "RADON_CONTROL_PLANE_MANIFEST": str(manifest),
                "RADON_CONTROL_PLANE_READY": str(ready),
                "RADON_SHA256SUM": sha256sum,
            },
            capture_output=True,
            text=True,
            timeout=30,
        )
        combined = result.stdout + result.stderr
        assert result.returncode == 0, combined
        assert "refresh-control-plane-privileged" in combined or "privileged" in combined.lower()
