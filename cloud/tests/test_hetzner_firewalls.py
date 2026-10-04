"""Hetzner Cloud Firewalls as code (Ops Plane step 2E).

cloud/hetzner/firewalls/<name>.json are `hcloud firewall replace-rules`
rule files; hcloud_firewalls.py renders the recovery placeholder and prints
(dry run, default) or runs (--apply) the hcloud commands. These tests pin
the rule shapes so an edit cannot quietly open a service port publicly.

Hetzner Cloud Firewalls filter the public interface only; radon-private
traffic (app 10.0.0.2 -> broker 4001/8340) never passes them, so those
ports must never appear here. App SSH stays open to any: CI deploys over
SSH from GitHub-hosted runners with unfixed addresses.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FW_DIR = ROOT / "hetzner" / "firewalls"
ANY = ["0.0.0.0/0", "::/0"]
PLACEHOLDER = "__OPERATOR_RECOVERY_IP__/32"
NAMES = ("fw-radon-app", "fw-radon-broker", "fw-radon-ops")


def _load_tool():
    spec = importlib.util.spec_from_file_location(
        "hcloud_firewalls", FW_DIR / "hcloud_firewalls.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _rules(name):
    return json.loads((FW_DIR / f"{name}.json").read_text())


def _shape(rules):
    return {
        (r["direction"], r["protocol"], r.get("port"), tuple(r["source_ips"]))
        for r in rules
    }


def test_exactly_three_rule_files():
    assert sorted(p.stem for p in FW_DIR.glob("*.json")) == sorted(NAMES)


@pytest.mark.parametrize("name", NAMES)
def test_rules_are_inbound_only_with_descriptions(name):
    for rule in _rules(name):
        assert set(rule) == {"direction", "protocol", "port", "source_ips", "description"}
        assert rule["direction"] == "in"
        assert rule["protocol"] in ("tcp", "udp")
        assert rule["description"]


def test_app_shape():
    assert _shape(_rules("fw-radon-app")) == {
        ("in", "tcp", "22", tuple(ANY)),
        ("in", "tcp", "80", tuple(ANY)),
        ("in", "tcp", "443", tuple(ANY)),
        ("in", "udp", "41641", tuple(ANY)),
    }


def test_broker_shape():
    assert _shape(_rules("fw-radon-broker")) == {
        ("in", "tcp", "22", (PLACEHOLDER,)),
        ("in", "udp", "41641", tuple(ANY)),
    }


def test_ops_shape():
    assert _shape(_rules("fw-radon-ops")) == {
        ("in", "tcp", "22", (PLACEHOLDER,)),
        ("in", "tcp", "80", tuple(ANY)),
        ("in", "tcp", "443", tuple(ANY)),
        ("in", "udp", "41641", tuple(ANY)),
    }


@pytest.mark.parametrize("name", NAMES)
def test_no_service_port_is_public(name):
    ports = {r["port"] for r in _rules(name)}
    assert not ports & {"3000", "4001", "4002", "5900", "8321", "8330", "8340", "8341", "8765"}


def test_web_ports_only_on_app_and_ops():
    assert not {r["port"] for r in _rules("fw-radon-broker")} & {"80", "443"}


class TestTool:
    def test_render_substitutes_the_recovery_address(self):
        tool = _load_tool()
        rendered = tool.render("fw-radon-broker", "93.184.216.34")
        ssh = [r for r in rendered if r["port"] == "22"]
        assert ssh[0]["source_ips"] == ["93.184.216.34/32"]
        assert PLACEHOLDER not in json.dumps(rendered)

    @pytest.mark.parametrize(
        "bad", ["", "203.0.113.0/24", "100.98.36.17", "10.0.0.2", "0.0.0.0", "host.example"]
    )
    def test_recovery_must_be_one_public_ipv4(self, bad):
        tool = _load_tool()
        with pytest.raises(ValueError):
            tool.render("fw-radon-broker", bad)

    def test_app_needs_no_recovery_address(self):
        tool = _load_tool()
        assert tool.render("fw-radon-app", None) == _rules("fw-radon-app")

    def test_unknown_firewall_refused(self):
        tool = _load_tool()
        with pytest.raises(ValueError):
            tool.render("fw-radon-gpu", "93.184.216.34")

    def test_commands_are_the_documented_hcloud_calls(self):
        tool = _load_tool()
        cmds = tool.commands("fw-radon-broker", "radon-broker", "/tmp/r.json")
        assert cmds == [
            ["hcloud", "firewall", "replace-rules", "fw-radon-broker", "--rules-file", "/tmp/r.json"],
            ["hcloud", "firewall", "apply-to-resource", "fw-radon-broker", "--type", "server", "--server", "radon-broker"],
        ]

    def test_dry_run_is_default_and_never_calls_hcloud(self, capsys, monkeypatch):
        tool = _load_tool()
        calls = []
        monkeypatch.setattr(tool.subprocess, "run", lambda *a, **k: calls.append(a))
        rc = tool.main(["--firewall", "fw-radon-broker", "--server", "radon-broker",
                        "--recovery-ip", "93.184.216.34"])
        assert rc == 0
        assert calls == []
        out = capsys.readouterr().out
        assert "dry run" in out
        assert "93.184.216.34/32" in out
        assert "hcloud firewall replace-rules fw-radon-broker" in out

    def test_apply_creates_missing_firewall_then_runs_the_commands(self, monkeypatch):
        tool = _load_tool()
        calls = []

        class _Res:
            def __init__(self, rc):
                self.returncode = rc

        def fake_run(argv, **kwargs):
            calls.append(argv)
            if argv[:3] == ["hcloud", "firewall", "describe"]:
                return _Res(1)
            return _Res(0)

        monkeypatch.setattr(tool.subprocess, "run", fake_run)
        rc = tool.main(["--firewall", "fw-radon-app", "--server", "ib-gateway", "--apply"])
        assert rc == 0
        verbs = [c[2] for c in calls]
        assert verbs == ["describe", "create", "replace-rules", "apply-to-resource"]
        assert calls[-1][-1] == "ib-gateway"

    def test_apply_stops_on_first_failure(self, monkeypatch):
        tool = _load_tool()
        calls = []

        class _Res:
            returncode = 1

        monkeypatch.setattr(tool.subprocess, "run", lambda argv, **k: (calls.append(argv), _Res())[1])
        monkeypatch.setattr(
            tool, "_exists", lambda name: True
        )
        rc = tool.main(["--firewall", "fw-radon-app", "--server", "ib-gateway", "--apply"])
        assert rc != 0
        assert [c[2] for c in calls] == ["replace-rules"]


def test_no_deploy_path_touches_hetzner_firewalls():
    for rel in ("scripts/deploy.sh", "scripts/deploy-root-helper.sh", "scripts/setup-vps.sh"):
        assert "hcloud" not in (ROOT / rel).read_text(), rel
    assert "hcloud firewall" not in (ROOT.parent / ".github" / "workflows" / "ci.yml").read_text()
