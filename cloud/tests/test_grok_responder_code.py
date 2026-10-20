"""Isolated Grok responder executes root-owned code (DS-2026-10-17-16).

The service account can rewrite /var/lib/radon-responder/repo, because that
clone is the worktree grok edits. The interpreter and import tree must
therefore live somewhere that account cannot write, and deploy must refresh
that tree from the provision store.
"""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

CLOUD = Path(__file__).resolve().parents[1]
REPO = CLOUD.parent
INSTALL = CLOUD / "scripts" / "install-grok-responder-code.sh"
SETUP_VPS = CLOUD / "scripts" / "setup-vps.sh"
ACCOUNT = CLOUD / "scripts" / "grok_responder_account.py"
SERVICE = CLOUD / "services" / "responder-domain" / "radon-grok-responder.service"
CODE = "/usr/local/lib/radon/grok-responder"
PYTHON = "/usr/bin/python3.13 -E -S"
HOME = "/var/lib/radon-responder"


def _directive(name: str) -> list[str]:
    text = SERVICE.read_text(encoding="utf-8")
    return [line.split("=", 1)[1] for line in text.splitlines() if line.startswith(f"{name}=")]


def _paths(name: str) -> set[str]:
    return {part.lstrip("-") for part in " ".join(_directive(name)).split()}


def _under(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip("/") + "/")


def _writable(path: str) -> bool:
    rules = [(item, "rw") for item in _paths("ReadWritePaths")]
    rules += [(item, "ro") for item in _paths("ReadOnlyPaths")]
    rules += [(item, "no") for item in _paths("InaccessiblePaths")]
    hits = [(len(item), mode) for item, mode in rules if _under(path, item)]
    if not hits:
        return True
    return max(hits)[1] == "rw"


def test_service_executes_the_root_owned_tree_on_the_system_interpreter():
    (exec_start,) = _directive("ExecStart")
    assert exec_start == f"{PYTHON} {CODE}/scripts/grok_page_responder.py"
    assert HOME + "/repo" not in exec_start
    assert ".venv" not in exec_start
    assert "RADON_GROK_REPO=" + HOME + "/repo" in _directive("Environment")
    assert _directive("WorkingDirectory") == [HOME + "/repo"]


def test_executed_code_is_read_only_and_the_worktree_stays_writable():
    assert not _writable(CODE + "/scripts/grok_page_responder.py")
    assert not _writable(CODE)
    assert _writable(HOME + "/repo/scripts/grok_page_responder.py")
    assert _writable(HOME + "/repo/.git/config")
    assert _writable(HOME + "/cache/pages")
    assert _writable(HOME + "/.grok/sessions/s.json")
    assert _writable(HOME + "/runtime/grok-runtime.lock")
    assert CODE not in _paths("ReadWritePaths")


def test_import_closure_is_stdlib_only(tmp_path):
    scripts = tmp_path / "scripts"
    shutil.copytree(
        REPO / "scripts",
        scripts,
        ignore=shutil.ignore_patterns("tests", "__pycache__"),
    )
    probe = (
        "import sys; sys.path.insert(0, sys.argv[1]);"
        "import grok_page_responder;"
        "import ir_ensure_pr;"
        "from db.hrana_http import HranaHttpError;"
        "from watchdog import notify"
    )
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "GROK_PAGE_NO_DOTENV": "1"}
    result = subprocess.run(
        [sys.executable, "-E", "-S", "-c", probe, str(scripts)],
        capture_output=True, text=True, env=env, cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr


def _install(src: Path, target: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(INSTALL), str(src), str(target)],
        capture_output=True, text=True,
        env={**os.environ, "RADON_HELPER_SKIP_CHOWN": "1"},
    )


def test_install_replaces_the_tree_atomically(tmp_path):
    src = tmp_path / "stage" / "scripts"
    src.mkdir(parents=True)
    (src / "grok_page_responder.py").write_text("new\n", encoding="utf-8")
    target = tmp_path / "lib" / "grok-responder"
    (target / "scripts").mkdir(parents=True)
    (target / "scripts" / "stale.py").write_text("old\n", encoding="utf-8")

    result = _install(src, target)

    assert result.returncode == 0, result.stderr
    assert (target / "scripts" / "grok_page_responder.py").read_text(encoding="utf-8") == "new\n"
    assert not (target / "scripts" / "stale.py").exists()
    assert not target.is_symlink()
    assert [path.name for path in target.parent.iterdir()] == ["grok-responder"]
    assert not (target / "scripts" / "grok_page_responder.py").stat().st_mode & 0o022


def test_install_refuses_a_linked_target_or_parent(tmp_path):
    src = tmp_path / "stage" / "scripts"
    src.mkdir(parents=True)
    (src / "grok_page_responder.py").write_text("new\n", encoding="utf-8")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    linked_target = tmp_path / "a" / "grok-responder"
    linked_target.parent.mkdir()
    linked_target.symlink_to(elsewhere)
    assert _install(src, linked_target).returncode != 0
    linked_parent = tmp_path / "b"
    linked_parent.symlink_to(elsewhere)
    assert _install(src, linked_parent / "grok-responder").returncode != 0
    assert list(elsewhere.iterdir()) == []


def test_install_refuses_a_source_without_the_responder(tmp_path):
    src = tmp_path / "stage" / "scripts"
    src.mkdir(parents=True)
    assert _install(src, tmp_path / "lib" / "grok-responder").returncode != 0


@pytest.mark.parametrize("fault", ["publish-failure", "term-after-retire", "rollback-failure"])
def test_failed_install_preserves_previous_tree(tmp_path, fault):
    src = tmp_path / "stage" / "scripts"
    src.mkdir(parents=True)
    (src / "grok_page_responder.py").write_text("candidate\n", encoding="utf-8")
    target = tmp_path / "lib" / "grok-responder"
    (target / "scripts").mkdir(parents=True)
    previous = target / "scripts" / "grok_page_responder.py"
    previous.write_bytes(b"trusted previous responder\n")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    real_mv = shutil.which("mv")
    shim = fake_bin / "mv"
    shim.write_text(
        f"""#!/bin/bash
case "$2" in
  */.grok-responder-code.new.*) exit 71 ;;
  */.grok-responder-code.old.*/tree)
    [[ "{fault}" == rollback-failure ]] && exit 72
    ;;
esac
"{real_mv}" "$@" || exit $?
if [[ "{fault}" == term-after-retire && "$2" == "{target}" ]]; then
  kill -TERM "$PPID"
fi
""",
        encoding="utf-8",
    )
    shim.chmod(0o755)
    result = subprocess.run(
        ["bash", str(INSTALL), str(src), str(target)],
        capture_output=True, text=True, timeout=10,
        env={
            **os.environ,
            "PATH": str(fake_bin) + os.pathsep + os.environ["PATH"],
            "RADON_HELPER_SKIP_CHOWN": "1",
        },
    )
    assert result.returncode != 0
    if fault == "rollback-failure":
        backups = list(target.parent.glob(".grok-responder-code.old.*/tree/scripts/grok_page_responder.py"))
        assert len(backups) == 1, result.stderr
        assert backups[0].read_bytes() == b"trusted previous responder\n"
        assert str(backups[0].parents[1]) in result.stderr
    else:
        assert previous.is_file(), result.stderr
        assert previous.read_bytes() == b"trusted previous responder\n"
        assert sorted(path.name for path in target.parent.iterdir()) == ["grok-responder"]


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, check=True,
        env={**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"},
    )


def test_setup_vps_installs_committed_code_and_refuses_a_tampered_checkout(tmp_path):
    repo = tmp_path / "repo"
    (repo / "cloud" / "scripts").mkdir(parents=True)
    (repo / "scripts").mkdir()
    installer = repo / "cloud" / "scripts" / "install-grok-responder-code.sh"
    installer.write_bytes(INSTALL.read_bytes())
    responder = repo / "scripts" / "grok_page_responder.py"
    responder.write_text("print('committed')\n", encoding="utf-8")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "seed")
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "-q", "--bare", str(remote))
    _git(repo, "push", "-q", str(remote), "HEAD:refs/heads/main")
    target = tmp_path / "installed"
    env = {
        **os.environ,
        "RADON_SETUP_SOURCE_ONLY": "1",
        "RADON_APP_DIR": str(repo),
        "RADON_SETUP_STAGE_DIR": str(tmp_path / "stage"),
        "RADON_HELPER_SKIP_CHOWN": "1",
        "RADON_GROK_RESPONDER_CODE": str(target),
        "RADON_PROVENANCE_REMOTE_URL": str(remote),
        "RADON_PROVISION_ROOT": str(tmp_path / "provision"),
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null",
    }
    result = subprocess.run(
        ["bash", "-c", f"set -euo pipefail\nsource {SETUP_VPS}\ninstall_grok_responder_code\n"],
        env=env, capture_output=True, text=True, check=False, timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    installed = target / "scripts" / "grok_page_responder.py"
    assert installed.read_text(encoding="utf-8") == "print('committed')\n"
    assert not installed.stat().st_mode & 0o022
    responder.write_text("print('tampered')\n", encoding="utf-8")
    refused = subprocess.run(
        ["bash", "-c", f"set -euo pipefail\nsource {SETUP_VPS}\ninstall_grok_responder_code\n"],
        env=env, capture_output=True, text=True, check=False, timeout=120,
    )
    assert refused.returncode != 0
    assert installed.read_text(encoding="utf-8") == "print('committed')\n"
    main = SETUP_VPS.read_text(encoding="utf-8").split("main() {", 1)[1].split("\n}\n", 1)[0]
    assert main.index("install_grok_responder_code") < main.index("validate_env")


def _load_account():
    import importlib.util
    spec = importlib.util.spec_from_file_location("grok_responder_account_code", ACCOUNT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _stage(tmp_path: Path) -> Path:
    stage = tmp_path / "stage"
    (stage / "scripts").mkdir(parents=True)
    (stage / "scripts" / "grok_page_responder.py").write_text("print('stage')\n", encoding="utf-8")
    (stage / "cloud" / "scripts").mkdir(parents=True)
    installer = stage / "cloud" / "scripts" / "install-grok-responder-code.sh"
    installer.write_text("#!/bin/bash\nprintf '%s\\n' \"$1\" > \"$2.invoked\"\nmkdir -p \"$2/scripts\"\nprintf stage > \"$2/scripts/grok_page_responder.py\"\n", encoding="utf-8")
    installer.chmod(0o755)
    units = stage / "cloud" / "services" / "responder-domain"
    units.mkdir(parents=True)
    for name in (
        "radon-grok-responder.service", "radon-grok-responder.timer",
        "radon-grok-responder-upgrade.service", "radon-grok-responder-upgrade.timer",
    ):
        (units / name).write_text(f"[Unit]\nDescription={name}\n", encoding="utf-8")
    policy = stage / "cloud" / "config" / "polkit" / "50-radon-services.rules"
    policy.parent.mkdir(parents=True)
    policy.write_text("policy\n", encoding="utf-8")
    return stage


def test_refresh_updates_code_and_units_without_rebuilding_the_clone(tmp_path, monkeypatch):
    maintenance = _load_account()
    stage = _stage(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    ready = home / "ready"
    ready.write_text("ready\n", encoding="utf-8")
    clone = home / "repo" / ".git"
    clone.mkdir(parents=True)
    (home / "repo" / "marker").write_text("preserve\n", encoding="utf-8")
    private = tmp_path / "private"
    systemd = tmp_path / "systemd"
    code = tmp_path / "code"
    helper = tmp_path / "lib" / "grok_responder_account.py"
    monkeypatch.setattr(maintenance, "READY", ready)
    monkeypatch.setattr(maintenance, "ACCOUNT_HOME", home)
    monkeypatch.setattr(maintenance, "PRIVATE_DIR", private)
    monkeypatch.setattr(maintenance, "INVENTORY", private / "installed-units.sha256")
    monkeypatch.setattr(maintenance, "SYSTEMD", systemd)
    monkeypatch.setattr(maintenance, "CODE_ROOT", code)
    monkeypatch.setattr(maintenance, "INSTALLED_HELPER", helper)
    monkeypatch.setattr(maintenance, "trusted_tree", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(maintenance, "root_directory", lambda path, mode=0o700: path.mkdir(parents=True, exist_ok=True))
    private.mkdir()
    systemd.mkdir()
    calls = []

    def runner(argv, **kwargs):
        rendered = [str(arg) for arg in argv]
        calls.append(rendered)
        if "--property=ActiveState" in rendered:
            return SimpleNamespace(returncode=0, stdout="inactive")
        if rendered[:1] == ["/bin/bash"]:
            result = subprocess.run(rendered, capture_output=True, text=True, check=False)
            if result.returncode:
                raise maintenance.MaintenanceError("installer failed")
            return result
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(maintenance, "run", runner)
    maintenance.refresh(stage)
    assert (home / "repo" / "marker").read_text(encoding="utf-8") == "preserve\n"
    assert ready.read_text(encoding="utf-8") == "ready\n"
    assert (code / "scripts" / "grok_page_responder.py").read_text(encoding="utf-8") == "stage"
    assert (systemd / "radon-grok-responder.service").read_text(encoding="utf-8").startswith("[Unit]\n")
    assert "radon-grok-responder.service" in (private / "installed-units.sha256").read_text(encoding="utf-8")
    assert all(Path(call[0]).name not in {"git", "bun", "useradd", "pip"} for call in calls)
    assert not any(call[index:index + 2] == ["-m", "venv"] for call in calls for index in range(len(call) - 1))
    assert any(str(stage / "cloud/scripts/install-grok-responder-code.sh") in call for call in calls)
    assert stat.S_ISREG((systemd / "radon-grok-responder.service").stat().st_mode)


def test_refresh_refuses_an_active_workload_before_install(tmp_path, monkeypatch):
    maintenance = _load_account()
    stage = _stage(tmp_path)
    ready = tmp_path / "ready"
    ready.write_text("ready\n", encoding="utf-8")
    monkeypatch.setattr(maintenance, "READY", ready)
    monkeypatch.setattr(maintenance, "trusted_tree", lambda *_args, **_kwargs: None)
    calls = []

    def runner(argv, **kwargs):
        calls.append([str(arg) for arg in argv])
        return SimpleNamespace(returncode=0, stdout="active")

    monkeypatch.setattr(maintenance, "run", runner)
    with pytest.raises(maintenance.MaintenanceError):
        maintenance.refresh(stage)
    assert len(calls) == 1
    assert "install-grok-responder-code.sh" not in " ".join(calls[0])


def test_refresh_requires_the_ready_marker(tmp_path, monkeypatch):
    maintenance = _load_account()
    monkeypatch.setattr(maintenance, "READY", tmp_path / "missing")
    monkeypatch.setattr(maintenance, "trusted_tree", lambda *_args, **_kwargs: None)
    with pytest.raises(maintenance.MaintenanceError):
        maintenance.refresh(_stage(tmp_path))


def test_prepare_still_installs_code_on_a_fresh_account(tmp_path):
    source = ACCOUNT.read_text(encoding="utf-8")
    prepare = source.split("def prepare(", 1)[1].split("\ndef ", 1)[0]
    assert "install_responder_code(stage)" in prepare
    assert "publish_optional_units(stage)" in prepare
    assert 'READY.exists()' in prepare
    shell = (CLOUD / "scripts" / "setup-grok-responder-account.sh").read_text(encoding="utf-8")
    assert 'verb=prepare' in shell
    assert '[[ -f "$ACCOUNT_HOME/ready" && ! -L "$ACCOUNT_HOME/ready" ]]' in shell
    assert 'verb=refresh' in shell
