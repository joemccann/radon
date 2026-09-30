"""REL-292 / R-711: a failed candidate must never overwrite the live CLI."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import grok_runtime
import grok_upgrade as upgrade
from test_grok_upgrade import VALID_BODY


def result(stdout="", rc=0):
    return SimpleNamespace(returncode=rc, stdout=stdout, stderr="")


@pytest.fixture
def machine(tmp_path, monkeypatch):
    home = tmp_path / "home"
    live = home / ".local/bin/grok"
    live.parent.mkdir(parents=True)
    live.write_text("1.0.3")
    live.chmod(0o755)
    alias = home / ".grok/bin/grok"
    alias.parent.mkdir(parents=True)
    alias.symlink_to(live)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(upgrade, "_record_health", lambda *a, **kw: None)
    lkg = tmp_path / "lkg.json"
    grok_runtime.write_lkg(lkg, grok_runtime.LkgState(
        "1.0.3", str(live), "grok-test", "high", "2026-09-29T00:00:00Z", "pass",
    ))
    return dict(grok_bin=str(live), live_bin=live, alias_bin=alias, lkg_path=lkg,
                lock_path=tmp_path / "lock", scratch=tmp_path / "scratch", alerter=lambda _: None)


def updater(*, smoke_ok=True, install=True, version="1.0.4"):
    """A self-updater replaces its own executable, regardless of HOME."""
    def run(argv, **kwargs):
        if "--check" in argv:
            return result(json.dumps({"current": "1.0.3", "latest": version}))
        if "models" in argv:
            return result("Default model: grok-test\n  grok-test\n")
        if "update" in argv:
            if install:
                Path(argv[0]).resolve().write_text(version)
            return result("updated")
        if "--version" in argv:
            return result("grok " + Path(argv[0]).read_text())
        if "--prompt-file" in argv:
            if smoke_ok == "timeout":
                raise subprocess.TimeoutExpired(argv, 600)
            return result(VALID_BODY + "\nRESULT: stand_down | verified\n" if smoke_ok else "RESULT: failed | rejected\n")
        raise AssertionError(argv)
    return run


def test_failed_self_update_smoke_keeps_live_bytes_and_alias(machine):
    old_state = machine["lkg_path"].read_bytes()
    outcome = upgrade.run_upgrade(**machine, runner=updater(smoke_ok=False))
    assert outcome["action"] == "failed"
    assert machine["live_bin"].read_text() == "1.0.3"
    assert machine["alias_bin"].read_text() == "1.0.3"
    assert machine["lkg_path"].read_bytes() == old_state


def test_success_then_failed_upgrade_preserves_previous_immutable_binary(machine):
    first = upgrade.run_upgrade(**machine, runner=updater())
    assert first["action"] == "promoted"
    assert machine["alias_bin"].resolve() == machine["live_bin"].resolve()
    prior_binary = machine["live_bin"].resolve(strict=True)
    assert prior_binary.read_text() == "1.0.4"
    prior_state = machine["lkg_path"].read_bytes()
    second = upgrade.run_upgrade(**machine, runner=updater(smoke_ok=False, version="1.0.5"))
    assert second["action"] == "failed"
    assert prior_binary.read_text() == "1.0.4"
    assert machine["live_bin"].read_text() == "1.0.4"
    assert machine["lkg_path"].read_bytes() == prior_state


def test_missing_candidate_version_cannot_promote_live_into_a_self_symlink(machine):
    outcome = upgrade.run_upgrade(**machine, runner=updater(install=False))
    assert outcome["action"] == "failed"
    assert machine["live_bin"].read_text() == "1.0.3"


def test_failed_symlink_creation_does_not_unlink_live(machine, monkeypatch):
    candidate = machine["scratch"] / "candidate-binary"
    candidate.parent.mkdir(parents=True)
    candidate.write_text("1.0.4")
    def fail(*a, **kw):
        raise OSError("injected symlink failure")
    monkeypatch.setattr(Path, "symlink_to", fail)
    with pytest.raises(OSError, match="injected"):
        upgrade.promote_live_symlink(machine["live_bin"], candidate)
    assert machine["live_bin"].read_text() == "1.0.3"
