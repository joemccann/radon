"""Declared host firewalls (Ops Plane step 2D).

The app host allowed everything in on tailscale0 and 8321 from all of
10.0.0.0/16; the broker ran with ufw inactive. cloud/scripts/host-firewall.sh
is the operator's tool (dry run by default) and setup-vps.sh open_firewall
applies the same app ruleset at bootstrap. Deploy never runs either.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "host-firewall.sh"
SETUP = ROOT / "scripts" / "setup-vps.sh"

OPERATORS = "100.98.36.17, 100.87.184.89/32"
OPS = "100.70.0.1 100.70.0.2/32"


def _run(args, env=None, fake_bin: Path | None = None):
    full_env = {**os.environ, **(env or {})}
    for key in ("RADON_FW_OPERATOR_SOURCES", "RADON_FW_OPS_SOURCES"):
        if env is None or key not in env:
            full_env.pop(key, None)
    if fake_bin is not None:
        full_env["PATH"] = f"{fake_bin}:{full_env['PATH']}"
    return subprocess.run(
        ["bash", str(SCRIPT), *args], env=full_env, capture_output=True, text=True
    )


def _dry(role, operators=OPERATORS, ops=OPS):
    env = {}
    if operators is not None:
        env["RADON_FW_OPERATOR_SOURCES"] = operators
    if ops is not None:
        env["RADON_FW_OPS_SOURCES"] = ops
    res = _run(["--role", role], env)
    return res


def _ufw_lines(res):
    return [l for l in res.stdout.splitlines() if l.startswith("ufw ")]


@pytest.fixture
def fake_ufw(tmp_path: Path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log = tmp_path / "ufw.log"
    ufw = fake_bin / "ufw"
    ufw.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> {log}\nexit 0\n")
    ufw.chmod(0o755)
    return fake_bin, log


class TestAppRules:
    def test_exact_ordered_app_ruleset(self):
        res = _dry("app")
        assert res.returncode == 0, res.stderr
        assert _ufw_lines(res) == [
            "ufw --force reset",
            "ufw default deny incoming",
            "ufw default allow outgoing",
            "ufw allow from 100.70.0.1 to any port 8341 proto tcp comment radon-ops-agent",
            "ufw deny from 100.70.0.1 comment radon-ops-deny-else",
            "ufw allow from 100.70.0.2 to any port 8341 proto tcp comment radon-ops-agent",
            "ufw deny from 100.70.0.2 comment radon-ops-deny-else",
            "ufw allow 22/tcp comment ssh-ci-deploy-and-recovery",
            "ufw allow 80/tcp comment caddy-http",
            "ufw allow 443/tcp comment caddy-https",
            "ufw allow 41641/udp comment tailscale-direct",
            "ufw allow from 100.98.36.17 to any port 8321 proto tcp comment operator-cloud-thin-api",
            "ufw allow from 100.87.184.89 to any port 8321 proto tcp comment operator-cloud-thin-api",
            "ufw allow from 10.0.0.4 to any port 8321 proto tcp comment radon-broker-health",
            "ufw --force enable",
        ]

    def test_no_blanket_tailnet_or_subnet_allow(self):
        text = _dry("app").stdout
        assert "tailscale0" not in text
        assert "10.0.0.0/16" not in text
        assert "100.64.0.0/10" not in text

    def test_ops_rules_precede_every_broad_allow(self):
        lines = _ufw_lines(_dry("app"))
        last_ops = max(i for i, l in enumerate(lines) if "radon-ops" in l)
        first_broad = min(i for i, l in enumerate(lines) if l.startswith("ufw allow ") and " from " not in l)
        assert last_ops < first_broad

    def test_empty_sources_still_keep_ssh_web_and_broker_health(self):
        res = _dry("app", operators="", ops="")
        assert res.returncode == 0, res.stderr
        lines = _ufw_lines(res)
        assert "ufw allow 22/tcp comment ssh-ci-deploy-and-recovery" in lines
        assert not [l for l in lines if "8341" in l or "operator-cloud-thin" in l]
        assert lines[-2].endswith("radon-broker-health")


class TestBrokerRules:
    def test_exact_ordered_broker_ruleset(self):
        res = _dry("broker", ops="")
        assert res.returncode == 0, res.stderr
        assert _ufw_lines(res) == [
            "ufw --force reset",
            "ufw default deny incoming",
            "ufw default allow outgoing",
            "ufw allow from 100.98.36.17 to any port 22 proto tcp comment operator-ssh",
            "ufw allow from 100.87.184.89 to any port 22 proto tcp comment operator-ssh",
            "ufw allow from 10.0.0.2 to any port 4001 proto tcp comment app-ib-api",
            "ufw allow from 10.0.0.2 to any port 8340 proto tcp comment app-ib-gateway-remote",
            "ufw allow 41641/udp comment tailscale-direct",
            "ufw --force enable",
        ]

    def test_broker_never_opens_public_ports(self):
        lines = _ufw_lines(_dry("broker"))
        broad = [l for l in lines if l.startswith("ufw allow ") and " from " not in l]
        assert broad == ["ufw allow 41641/udp comment tailscale-direct"]

    def test_broker_refuses_without_operator_sources(self):
        res = _dry("broker", operators="")
        assert res.returncode == 2
        assert "RADON_FW_OPERATOR_SOURCES" in res.stderr
        assert not _ufw_lines(res)


class TestValidation:
    @pytest.mark.parametrize(
        "bad", ["100.64.0.0/10", "10.0.0.0/16", "999.1.1.1", "host.example", "fd7a::1", "1.2.3"]
    )
    def test_rejects_anything_but_a_single_ipv4(self, bad):
        res = _dry("app", operators=bad)
        assert res.returncode == 2
        assert "not an IPv4 /32" in res.stderr

    def test_unknown_role_refused(self):
        assert _dry("gpu").returncode == 2

    def test_bad_flag_refused(self):
        assert _run(["--role", "app", "--yes"]).returncode == 2


class TestApply:
    def test_dry_run_is_the_default_and_never_calls_ufw(self, fake_ufw):
        fake_bin, log = fake_ufw
        res = _run(["--role", "app"], {"RADON_FW_OPERATOR_SOURCES": OPERATORS}, fake_bin)
        assert res.returncode == 0, res.stderr
        assert "dry run" in res.stdout
        assert not log.exists()

    def test_apply_runs_exactly_the_dry_run_commands(self, fake_ufw):
        fake_bin, log = fake_ufw
        env = {"RADON_FW_OPERATOR_SOURCES": OPERATORS, "RADON_FW_TEST_MODE": "1"}
        dry = _run(["--role", "broker"], env)
        res = _run(["--role", "broker", "--apply"], env, fake_bin)
        assert res.returncode == 0, res.stderr
        calls = log.read_text().splitlines()
        assert ["ufw " + c for c in calls] == _ufw_lines(dry) + ["ufw status numbered"]

    def test_apply_refuses_non_root(self, fake_ufw):
        if os.geteuid() == 0:
            pytest.skip("running as root")
        fake_bin, log = fake_ufw
        res = _run(["--role", "app", "--apply"], {}, fake_bin)
        assert res.returncode == 1
        assert not log.exists()


class TestSetupParityAndDeploy:
    @pytest.mark.parametrize("name", ["radon_fw_valid_sources", "radon_fw_rules"])
    def test_setup_vps_mirrors_the_functions_byte_for_byte(self, name):
        pattern = re.compile(rf"^{name}\(\) \{{\n.*?^\}}\n", re.S | re.M)
        script = pattern.search(SCRIPT.read_text())
        setup = pattern.search(SETUP.read_text())
        assert script and setup, name
        assert script.group(0) == setup.group(0)

    def test_open_firewall_applies_the_declared_app_set(self, fake_ufw):
        fake_bin, log = fake_ufw
        env = {
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "RADON_SETUP_SOURCE_ONLY": "1",
            "RADON_FW_OPERATOR_SOURCES": OPERATORS,
            "RADON_FW_OPS_SOURCES": OPS,
        }
        res = subprocess.run(
            ["bash", "-c", f"set -euo pipefail; source {SETUP}; open_firewall"],
            env=env, capture_output=True, text=True,
        )
        assert res.returncode == 0, res.stderr
        calls = ["ufw " + c for c in log.read_text().splitlines()]
        assert calls == _ufw_lines(_dry("app"))

    def test_open_firewall_refuses_bad_sources_without_touching_ufw(self, fake_ufw):
        fake_bin, log = fake_ufw
        env = {
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "RADON_SETUP_SOURCE_ONLY": "1",
            "RADON_FW_OPERATOR_SOURCES": "100.64.0.0/10",
        }
        res = subprocess.run(
            ["bash", "-c", f"source {SETUP}; open_firewall"],
            env=env, capture_output=True, text=True,
        )
        assert res.returncode != 0
        assert not log.exists()

    def test_no_deploy_path_runs_the_firewall(self):
        deploy_paths = [
            ROOT / "scripts" / "deploy.sh",
            ROOT / "scripts" / "deploy-root-helper.sh",
            ROOT / "scripts" / "sync-control-plane.sh",
            ROOT / "scripts" / "bootstrap-control-plane.sh",
            ROOT.parent / ".github" / "workflows" / "ci.yml",
        ]
        for path in deploy_paths:
            text = path.read_text()
            assert "host-firewall" not in text, path
            assert "open_firewall" not in text, path
            assert re.search(r"^\s*ufw\s", text, re.M) is None, path
