"""T-533: observe the rules file and exact commands consumed by fake hcloud."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

TOOL = Path(__file__).resolve().parents[1] / "hetzner/firewalls/hcloud_firewalls.py"


@pytest.fixture
def tool(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location("firewall_execution", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # Every rules artifact belongs to the fixture, including the dry-run file.
    monkeypatch.setattr(module.tempfile, "tempdir", str(tmp_path))
    monkeypatch.delenv("RADON_HCLOUD_RECOVERY_IP", raising=False)
    return module


def expected_rules(name):
    """Independent security policy oracle, rather than calling the renderer."""
    ssh_sources = ["0.0.0.0/0", "::/0"] if name == "fw-radon-app" else ["93.184.216.34/32"]
    rules = {("in", "tcp", "22", tuple(ssh_sources)),
             ("in", "udp", "41641", ("0.0.0.0/0", "::/0"))}
    if name != "fw-radon-broker":
        rules |= {("in", "tcp", port, ("0.0.0.0/0", "::/0")) for port in ("80", "443")}
    return rules


@pytest.mark.parametrize("name", ["fw-radon-app", "fw-radon-broker", "fw-radon-ops"])
@pytest.mark.parametrize("exists", [False, True])
def test_apply_delivers_exact_policy_and_resource_to_hcloud(tool, monkeypatch, name, exists):
    calls = []
    rules_paths = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        if argv[2] == "describe":
            return SimpleNamespace(returncode=0 if exists else 1)
        if argv[2] == "replace-rules":
            assert argv[:5] == ["hcloud", "firewall", "replace-rules", name, "--rules-file"]
            rules_path = Path(argv[5])
            rules_paths.append(str(rules_path))
            rules = json.loads(rules_path.read_text())
            assert len(rules) == len(expected_rules(name))
            assert {(r["direction"], r["protocol"], r["port"], tuple(r["source_ips"]))
                    for r in rules} == expected_rules(name)
            assert all(r["description"] for r in rules)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(tool.subprocess, "run", run)
    assert tool.main(["--firewall", name, "--server", "synthetic-server",
                      "--recovery-ip", "93.184.216.34", "--apply"]) == 0
    assert len(rules_paths) == 1
    expected = [(["hcloud", "firewall", "describe", name], {"capture_output": True, "text": True})]
    if not exists:
        expected.append((["hcloud", "firewall", "create", "--name", name], {}))
    expected.extend([
        (["hcloud", "firewall", "replace-rules", name, "--rules-file", rules_paths[0]], {}),
        (["hcloud", "firewall", "apply-to-resource", name, "--type", "server", "--server", "synthetic-server"], {}),
    ])
    assert calls == expected


@pytest.mark.parametrize("failed_verb", ["create", "replace-rules", "apply-to-resource"])
def test_apply_reports_each_failure_without_running_later_mutations(tool, monkeypatch, capsys, failed_verb):
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=1 if argv[2] in ("describe", failed_verb) else 0)

    monkeypatch.setattr(tool.subprocess, "run", run)
    assert tool.main(["--firewall", "fw-radon-broker", "--server", "synthetic-server",
                      "--recovery-ip", "93.184.216.34", "--apply"]) == 1
    sequence = ["describe", "create", "replace-rules", "apply-to-resource"]
    assert [argv[2] for argv in calls] == sequence[:sequence.index(failed_verb) + 1]
    assert f"failed: hcloud firewall {failed_verb}" in capsys.readouterr().err


@pytest.mark.parametrize("apply", [False, True])
def test_invalid_recovery_refuses_before_any_artifact_or_command(tool, monkeypatch, tmp_path, apply):
    monkeypatch.setattr(tool.subprocess, "run", lambda *a, **k: pytest.fail("hcloud called on invalid input"))
    argv = ["--firewall", "fw-radon-broker", "--server", "synthetic-server", "--recovery-ip", "10.0.0.2"]
    assert tool.main(argv + (["--apply"] if apply else [])) == 2
    assert list(tmp_path.iterdir()) == []
