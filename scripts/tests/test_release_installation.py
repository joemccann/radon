"""Operator install and runner lifecycle gates fail closed under hostile paths."""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import plistlib
import signal
import stat
import subprocess
import sys
import textwrap
import time
from types import SimpleNamespace

import pytest

import unattended_release as ur
sys.path.insert(0, str(Path(ur.__file__).parent / "runner"))
import install_unattended_release as installer
import lifecycle_gate

_REAL_SUBPROCESS_RUN = subprocess.run


def _notifications(path: Path) -> Path:
    path.write_text(
        "PUSHOVER_USER=fixture-user\n"
        "PUSHOVER_TOKEN=fixture-token\n"
        "GH_TOKEN=must-not-copy\n"
        "DATABASE_URL=must-not-copy\n",
        encoding="utf-8",
    )
    path.chmod(0o600)
    return path


def test_operator_installer_writes_scoped_plist_policy_and_notification_credentials(monkeypatch, tmp_path):
    source = Path(ur.__file__)
    notifications = _notifications(tmp_path / "notifications.env")
    destination = tmp_path / "operator" / ".local/share/radon-operator-releases"
    destination.parent.mkdir(parents=True)
    home = tmp_path / "operator-home"
    home.mkdir()
    calls = []
    monkeypatch.setattr(installer.subprocess, "run", lambda *args, **kwargs: calls.append((args, kwargs)))

    plist_path = installer.install(
        source=source,
        destination=destination,
        home=home,
        python=Path("/usr/bin/python3"),
        notifications=notifications,
    )

    config = plistlib.loads(plist_path.read_bytes())
    args = config["ProgramArguments"]
    assert config["Label"] == "com.radon.operator-releases"
    assert args[0:2] == ["/usr/bin/python3", "-I"]
    assert Path(args[2]).resolve().is_relative_to((destination / "releases").resolve())
    installed_release = Path(args[2]).parent
    assert {path.name for path in installed_release.iterdir()} == {
        "unattended_release.py",
        "private_security_release.py",
        "operator_runtime_refresh.py",
        "codex_capability_probe.py",
        "model_gateway",
        "ci",
    }
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o500 for path in installed_release.iterdir() if path.is_file())
    assert stat.S_IMODE((installed_release / "ci/check_pr_body.py").stat().st_mode) == 0o500
    isolated_help = _REAL_SUBPROCESS_RUN(
        [sys.executable, "-I", str(installed_release / "unattended_release.py"), "--help"],
        capture_output=True,
        text=True,
    )
    assert isolated_help.returncode == 0, isolated_help.stderr
    assert "--policy" in isolated_help.stdout
    assert config["StartInterval"] == 300
    assert config["RunAtLoad"] is True
    assert config["EnvironmentVariables"] == {
        "HOME": str(home),
        "PATH": "/opt/homebrew/bin:/usr/bin:/bin",
    }
    assert "GH_TOKEN" not in str(config) and "PUSHOVER_TOKEN" not in str(config)

    policy = json.loads((destination / "policy.json").read_text(encoding="utf-8"))
    stored_credentials = (destination / "notifications.env").read_text(encoding="utf-8")
    assert policy["repository"] == "joemccann/radon"
    assert set(policy["reviewers"]) == {"gpt-6.1-sol", "gpt-6-luna"}
    assert set(stored_credentials.splitlines()) == {
        "PUSHOVER_USER=fixture-user",
        "PUSHOVER_TOKEN=fixture-token",
    }
    assert stat.S_IMODE((destination / "policy.json").stat().st_mode) == 0o600
    assert stat.S_IMODE((destination / "hosts.json").stat().st_mode) == 0o600
    assert stat.S_IMODE((destination / "notifications.env").stat().st_mode) == 0o600
    assert len(calls) == 1 and calls[0][0][0] == ["/usr/bin/plutil", "-lint", str(plist_path)]


def test_operator_installer_refuses_root_execution_before_creating_authority(monkeypatch, tmp_path):
    source = tmp_path / "unattended_release.py"
    source.write_text("# fixture\n", encoding="utf-8")
    (tmp_path / "private_security_release.py").write_text("# fixture\n", encoding="utf-8")
    (tmp_path / "operator_runtime_refresh.py").write_text("# fixture\n", encoding="utf-8")
    (tmp_path / "codex_capability_probe.py").write_text("# fixture\n", encoding="utf-8")
    (tmp_path / "ci").mkdir()
    (tmp_path / "ci/check_pr_body.py").write_text("# fixture\n", encoding="utf-8")
    notifications = _notifications(tmp_path / "notifications.env")
    destination = tmp_path / "missing-parent" / "release-agent"
    monkeypatch.setattr(installer.os, "getuid", lambda: 0)
    monkeypatch.setattr(ur, "_trusted", lambda *args, **kwargs: None)

    with pytest.raises(PermissionError, match="operator"):
        installer.install(
            source=source,
            destination=destination,
            home=tmp_path / "home",
            python=Path("/usr/bin/python3"),
            notifications=notifications,
        )

    assert not destination.exists()


def test_operator_installer_rejects_symlinked_notification_source(monkeypatch, tmp_path):
    source = tmp_path / "unattended_release.py"
    source.write_text("# fixture\n", encoding="utf-8")
    (tmp_path / "private_security_release.py").write_text("# fixture\n", encoding="utf-8")
    (tmp_path / "operator_runtime_refresh.py").write_text("# fixture\n", encoding="utf-8")
    (tmp_path / "codex_capability_probe.py").write_text("# fixture\n", encoding="utf-8")
    (tmp_path / "ci").mkdir()
    (tmp_path / "ci/check_pr_body.py").write_text("# fixture\n", encoding="utf-8")
    target = _notifications(tmp_path / "real.env")
    link = tmp_path / "notifications.env"
    link.symlink_to(target)
    destination = tmp_path / "release-agent"
    home = tmp_path / "home"
    destination.parent.mkdir(exist_ok=True)
    home.mkdir()
    monkeypatch.setattr(installer.subprocess, "run", lambda *args, **kwargs: None)

    with pytest.raises((PermissionError, OSError)):
        installer.install(
            source=source,
            destination=destination,
            home=home,
            python=Path("/usr/bin/python3"),
            notifications=link,
        )

    assert not (destination / "notifications.env").exists()


def _gate_fixture(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "runner"
    root.mkdir()
    gate = root / "maintenance.lock"
    gate.write_bytes(b"")
    gate.chmod(0o644)
    run_loop = root / "run_loop.sh"
    run_loop.write_text("#!/bin/bash\nexit 17\n", encoding="utf-8")
    return root, gate


def test_lifecycle_gate_rejects_writable_and_symlinked_gate(monkeypatch, tmp_path):
    root, gate = _gate_fixture(tmp_path)
    monkeypatch.setattr(lifecycle_gate, "__file__", str(root / "lifecycle_gate.py"))
    monkeypatch.setattr(lifecycle_gate.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("must fail before launch"))
    monkeypatch.setattr(lifecycle_gate.os, "fstat", lambda fd: SimpleNamespace(
        st_dev=gate.stat().st_dev, st_ino=gate.stat().st_ino, st_uid=0,
    ))

    gate.chmod(0o666)
    with pytest.raises(PermissionError, match="untrusted"):
        lifecycle_gate.main(["security"])

    gate.chmod(0o644)
    link = root / "link.lock"
    link.symlink_to(gate)
    gate.unlink()
    link.rename(gate)
    with pytest.raises(PermissionError, match="untrusted"):
        lifecycle_gate.main(["security"])


def test_lifecycle_gate_keeps_shared_lock_in_child_after_wrapper_sigkill(tmp_path):
    root = tmp_path / "runner"
    root.mkdir()
    gate = root / "maintenance.lock"
    gate.write_bytes(b"")
    gate.chmod(0o644)
    child_pid_file = tmp_path / "child.pid"
    (root / "run_loop.sh").write_text(
        f"#!/bin/bash\necho $$ > {child_pid_file}\nexec /bin/sleep 45\n",
        encoding="utf-8",
    )
    repository_scripts = Path(__file__).resolve().parents[2]
    harness = textwrap.dedent(
        f"""
        import os
        import stat
        from pathlib import Path
        from types import SimpleNamespace
        import lifecycle_gate
        root = Path({str(root)!r})
        gate = root / 'maintenance.lock'
        lifecycle_gate.__file__ = str(root / 'lifecycle_gate.py')
        real_lstat = Path.lstat
        real_fstat = os.fstat
        def fake_lstat(path):
            info = real_lstat(path)
            if path == gate:
                return SimpleNamespace(st_mode=info.st_mode, st_uid=0, st_dev=info.st_dev, st_ino=info.st_ino)
            return info
        def fake_fstat(fd):
            info = real_fstat(fd)
            return SimpleNamespace(st_mode=info.st_mode, st_uid=0, st_dev=info.st_dev, st_ino=info.st_ino)
        Path.lstat = fake_lstat
        os.fstat = fake_fstat
        raise SystemExit(lifecycle_gate.main(['security']))
        """
    )
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PYTHONPATH": str(repository_scripts)}
    wrapper = subprocess.Popen([sys.executable, "-c", harness], env=env)
    child_pid = None
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not child_pid_file.exists():
            if wrapper.poll() is not None:
                pytest.fail(f"wrapper exited before spawning child: {wrapper.returncode}")
            time.sleep(0.02)
        assert child_pid_file.exists(), "runner child did not start"
        child_pid = int(child_pid_file.read_text(encoding="utf-8"))
        wrapper.kill()
        wrapper.wait(timeout=5)

        probe_fd = os.open(gate, os.O_RDONLY)
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(probe_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(probe_fd)
    finally:
        if wrapper.poll() is None:
            wrapper.kill()
            wrapper.wait(timeout=5)
        if child_pid is not None:
            try:
                os.kill(child_pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            probe_fd = os.open(gate, os.O_RDONLY)
            try:
                try:
                    fcntl.flock(probe_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    return
                except BlockingIOError:
                    pass
            finally:
                os.close(probe_fd)
            time.sleep(0.02)
        pytest.fail("runner child did not release inherited maintenance lock")


@pytest.mark.parametrize("kill_point", ["after_wal_fsync", "during_partial_append", "after_snapshot_replace"])
def test_state_wal_recovers_across_process_kill_points(tmp_path, kill_point):
    state_path = tmp_path / f"state-{kill_point}"
    owner_uid = tmp_path.stat().st_uid
    state_path.mkdir(mode=0o700)
    import unattended_release as release_module

    if kill_point == "during_partial_append":
        state = ur.FileStateStore(state_path, owner_uid=owner_uid)
        with state.locked():
            assert state.claim(1, "a" * 40)
        script = textwrap.dedent(
            f"""
            import os, signal
            path = {str(state_path / 'findings.jsonl')!r}
            fd = os.open(path, os.O_WRONLY | os.O_APPEND)
            os.write(fd, b'{{"state": {{"releases":')
            os.fsync(fd)
            os.kill(os.getpid(), signal.SIGKILL)
            """
        )
        expected_claim = "1:" + "a" * 40
    else:
        fail_action = (
            "import tempfile; tempfile.mkstemp = lambda *a, **k: os.kill(os.getpid(), signal.SIGKILL)"
            if kill_point == "after_wal_fsync"
            else """original_replace = os.replace
count = 0
def replace_then_kill(*args, **kwargs):
    global count
    original_replace(*args, **kwargs)
    count += 1
    if count == 1:
        os.kill(os.getpid(), signal.SIGKILL)
os.replace = replace_then_kill"""
        )
        script = (
            "import os, signal\n"
            "import unattended_release as ur\n"
            + fail_action
            + f"\nstore = ur.FileStateStore({str(state_path)!r}, owner_uid={owner_uid})\n"
            + f"with store.locked():\n    store.claim(2, {'b' * 40!r})\n"
        )
        expected_claim = "2:" + "b" * 40

    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PYTHONPATH": str(Path(release_module.__file__).parent)}
    crashed = subprocess.run([sys.executable, "-c", script], env=env, check=False)
    assert crashed.returncode == -signal.SIGKILL

    recovered = ur.FileStateStore(state_path, owner_uid=owner_uid)
    assert expected_claim in {key for key, _ in recovered.pending_releases()}
