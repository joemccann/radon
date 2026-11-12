"""The isolated Grok responder no longer installs or inventories the upgrade controller.

Owner decision (PR 1 of 2): drop the controller install and the
radon-grok-responder-upgrade.{service,timer} pair from the responder
domain. Hosts that already carry the pair have it retired on the next
refresh, and drift audit accepts the pre-refresh inventory until then.
"""
from __future__ import annotations

import hashlib
import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

CLOUD = Path(__file__).resolve().parents[1]
RESPONDER_UNITS = {"radon-grok-responder.service", "radon-grok-responder.timer"}
UPGRADE_UNITS = {"radon-grok-responder-upgrade.service", "radon-grok-responder-upgrade.timer"}


def _load(name, relative):
    spec = importlib.util.spec_from_file_location(name, CLOUD / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def maintenance(tmp_path, monkeypatch):
    module = _load("grok_responder_account_drops_upgrade", "scripts/grok_responder_account.py")
    systemd = tmp_path / "systemd"
    private = tmp_path / "private"
    systemd.mkdir()
    private.mkdir()
    monkeypatch.setattr(module, "SYSTEMD", systemd)
    monkeypatch.setattr(module, "PRIVATE_DIR", private)
    monkeypatch.setattr(module, "INVENTORY", private / "installed-units.sha256")
    monkeypatch.setattr(module, "INSTALLED_HELPER", tmp_path / "lib" / "grok_responder_account.py")
    monkeypatch.setattr(module, "trusted_tree", lambda *_a, **_k: None)
    monkeypatch.setattr(module, "root_directory", lambda path, mode=0o700: path.mkdir(parents=True, exist_ok=True))
    monkeypatch.setattr(module, "atomic_root_file", lambda path, content, mode=0o600, **_k: path.write_bytes(content))
    module.calls = []

    def runner(argv, **kwargs):
        rendered = [str(arg) for arg in argv]
        module.calls.append(rendered)
        if "--property=ActiveState" in rendered:
            return SimpleNamespace(returncode=0, stdout="inactive")
        return SimpleNamespace(returncode=0, stdout="loaded")

    monkeypatch.setattr(module, "run", runner)
    return module


def _stage(tmp_path, units=RESPONDER_UNITS):
    stage = tmp_path / "stage"
    source = stage / "cloud/services/responder-domain"
    source.mkdir(parents=True)
    for name in units:
        (source / name).write_text(f"[Unit]\nDescription={name}\n")
    policy = stage / "cloud/config/polkit/50-radon-services.rules"
    policy.parent.mkdir(parents=True)
    policy.write_text("policy\n")
    (stage / "scripts").mkdir()
    return stage


def test_responder_inventory_is_only_the_responder_pair(maintenance):
    assert set(maintenance.OPTIONAL_UNITS) == RESPONDER_UNITS


def test_publish_needs_only_the_responder_pair_and_records_two_units(maintenance, tmp_path):
    maintenance.publish_optional_units(_stage(tmp_path))
    inventory = maintenance.INVENTORY.read_text().splitlines()
    assert {line.split("  ", 1)[1] for line in inventory} == RESPONDER_UNITS
    assert not any(name in " ".join(call) for call in maintenance.calls for name in UPGRADE_UNITS)


def test_publish_retires_installed_upgrade_units(maintenance, tmp_path):
    for name in UPGRADE_UNITS:
        (maintenance.SYSTEMD / name).write_text("[Unit]\n")
    maintenance.publish_optional_units(_stage(tmp_path))
    assert not any((maintenance.SYSTEMD / name).exists() for name in UPGRADE_UNITS)
    disable = [call for call in maintenance.calls if call[1:3] == ["disable", "--now"]]
    assert len(disable) == 1 and set(disable[0][3:]) == UPGRADE_UNITS
    reloads = [i for i, call in enumerate(maintenance.calls) if call[1:] == ["daemon-reload"]]
    assert reloads and reloads[-1] > maintenance.calls.index(disable[0])


def test_verify_accepts_the_two_unit_inventory(maintenance, monkeypatch):
    files = {}
    lines = []
    for name in sorted(RESPONDER_UNITS):
        files[maintenance.SYSTEMD / name] = f"[Unit]\nDescription={name}\n"
        lines.append(hashlib.sha256(files[maintenance.SYSTEMD / name].encode()).hexdigest() + "  " + name)
    policy = "policy\n"
    files[maintenance.PRIVATE_DIR / "policy.sha256"] = hashlib.sha256(policy.encode()).hexdigest() + "\n"
    files[Path("/etc/polkit-1/rules.d/50-radon-services.rules")] = policy
    files[maintenance.INVENTORY] = "\n".join(lines) + "\n"
    monkeypatch.setattr(maintenance, "read_root_file", lambda path: files[path])
    maintenance.verify_installed_units()


def test_prepare_never_installs_the_upgrade_controller(maintenance, tmp_path, monkeypatch):
    account = SimpleNamespace(pw_uid=32000, pw_gid=32000, pw_dir=str(tmp_path / "home"), pw_shell="/usr/sbin/nologin")
    monkeypatch.setattr(maintenance, "ACCOUNT_HOME", tmp_path / "home")
    monkeypatch.setattr(maintenance, "READY", tmp_path / "home" / "ready")
    monkeypatch.setattr(maintenance, "pwd", SimpleNamespace(getpwnam=lambda name: account if name == maintenance.ACCOUNT else SimpleNamespace(pw_uid=1000)))
    monkeypatch.setattr(maintenance, "grp", SimpleNamespace(getgrnam=lambda _: SimpleNamespace(gr_gid=32002),
                                                            getgrgid=lambda _: SimpleNamespace(gr_name=maintenance.ACCOUNT)))
    isolated_os = SimpleNamespace(**vars(os))
    isolated_os.getgrouplist = lambda *_: [32000, 32002]
    monkeypatch.setattr(maintenance, "os", isolated_os)
    monkeypatch.setattr(maintenance, "owned_directory", lambda *_a: None)
    monkeypatch.setattr(maintenance, "as_account", lambda *_a, **_k: SimpleNamespace(returncode=0, stdout=""))
    monkeypatch.setattr(maintenance.shutil, "which", lambda *_a, **_k: "/bin/sh")
    monkeypatch.setattr(maintenance, "read_root_file", lambda _path: "")
    monkeypatch.setattr(maintenance, "derive_environment", lambda *_a: "")
    monkeypatch.setattr(maintenance, "install_responder_code", lambda _stage: maintenance.calls.append(["install-code"]))
    monkeypatch.setattr(maintenance, "admit_model_client", lambda _account: None)
    monkeypatch.setattr(maintenance, "ensure_native_cli", lambda: None)
    monkeypatch.setattr(maintenance, "publish_optional_units", lambda _stage: maintenance.calls.append(["publish"]))
    stage = _stage(tmp_path)
    maintenance.prepare(stage)
    assert ["install-code"] in maintenance.calls and ["publish"] in maintenance.calls
    assert not any("install-grok-upgrade-controller" in arg for call in maintenance.calls for arg in call)
    assert not (stage / "scripts/grok_upgrade.py").exists()


def test_refresh_never_installs_the_upgrade_controller(maintenance, tmp_path, monkeypatch):
    ready = tmp_path / "ready"
    ready.write_text("ready\n")
    monkeypatch.setattr(maintenance, "READY", ready)
    monkeypatch.setattr(maintenance, "pwd", SimpleNamespace(getpwnam=lambda _: SimpleNamespace(pw_uid=32000)))
    monkeypatch.setattr(maintenance, "install_responder_code", lambda _stage: None)
    monkeypatch.setattr(maintenance, "admit_model_client", lambda _account: None)
    monkeypatch.setattr(maintenance, "ensure_native_cli", lambda: None)
    monkeypatch.setattr(maintenance, "read_root_file", lambda _path: "")
    monkeypatch.setattr(maintenance, "derive_environment", lambda *_a: "")
    maintenance.refresh(_stage(tmp_path))
    assert not any("install-grok-upgrade-controller" in arg for call in maintenance.calls for arg in call)


def test_handover_and_idle_checks_watch_only_the_responder(maintenance, tmp_path):
    maintenance.handover(forward=False, runner=maintenance.run, marker=tmp_path / "ready")
    maintenance._responder_idle()
    watched = {call[-1] for call in maintenance.calls if "--property=ActiveState" in call}
    assert watched == {"radon-grok-responder.service"}


def _audit_inventory(tmp_path, monkeypatch, names):
    audit = _load("drift_audit_drops_upgrade", "scripts/drift_audit.py")
    directory = tmp_path / "optional"
    directory.mkdir(mode=0o700)
    inventory = directory / "installed-units.sha256"
    inventory.write_text("\n".join("a" * 64 + "  " + name for name in sorted(names)) + "\n")
    inventory.chmod(0o600)
    monkeypatch.setattr(audit, "OPTIONAL_RESPONDER_INVENTORY", inventory)
    real_fstat = os.fstat
    isolated_os = SimpleNamespace(**vars(os))
    isolated_os.fstat = lambda fd: SimpleNamespace(st_uid=0, st_mode=real_fstat(fd).st_mode)
    monkeypatch.setattr(audit, "os", isolated_os)
    drifts = []
    return audit._optional_responder_units(drifts), drifts


def test_drift_audit_accepts_the_two_unit_inventory(tmp_path, monkeypatch):
    selected, drifts = _audit_inventory(tmp_path, monkeypatch, RESPONDER_UNITS)
    assert drifts == [] and set(selected) == RESPONDER_UNITS


def test_drift_audit_accepts_the_pre_refresh_inventory(tmp_path, monkeypatch):
    selected, drifts = _audit_inventory(tmp_path, monkeypatch, RESPONDER_UNITS | UPGRADE_UNITS)
    assert drifts == [] and set(selected) == RESPONDER_UNITS | UPGRADE_UNITS


def test_drift_audit_rejects_a_partial_retired_pair(tmp_path, monkeypatch):
    selected, drifts = _audit_inventory(tmp_path, monkeypatch, RESPONDER_UNITS | {"radon-grok-responder-upgrade.timer"})
    assert selected == {} and drifts[-1]["id"] == "optional-unit-inventory"
