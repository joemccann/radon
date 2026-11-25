"""External runner logs: scrub before store, flag gate, push key, stream fallback."""
from __future__ import annotations

import importlib.util
import os
import plistlib
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
LOGD = ROOT / "scripts" / "runner" / "runner_logd.py"
INSTALL = ROOT / "scripts" / "runner" / "install.sh"
DOC = ROOT / "docs" / "runner.md"
BASH = "bash"
SECRET = "ghp_" + "C" * 36


def _load():
    spec = importlib.util.spec_from_file_location("runner_logd", LOGD)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def logd():
    return _load()


def _flag(path: Path, mode: int) -> None:
    if path.exists():
        os.chmod(path, 0o644)
    path.write_text("on\n")
    os.chmod(path, mode)


def test_scrub_drops_assignments_shapes_and_env_values_and_keeps_prose(logd):
    env = {"UW_TOKEN": "uw-secret-value-0123456789", "PATH": "/usr/bin", "HOME": "/Users/example"}
    text = "\n".join((
        f"GH_TOKEN={SECRET}",
        "PUSHOVER_USER=operator-key",
        "password=hunter2",
        "bearer abcdefghijklmnop",
        "saw uw-secret-value-0123456789 in the trace",
        "token of appreciation",
        'primary_key = "id"',
        "PATH stays /usr/bin",
    ))
    cleaned = logd.scrub_line(text, env)
    assert SECRET not in cleaned
    assert "hunter2" not in cleaned
    assert "operator-key" not in cleaned
    assert "abcdefghijklmnop" not in cleaned
    assert "uw-secret-value-0123456789" not in cleaned
    assert "GH_TOKEN=[redacted]" in cleaned
    assert "token of appreciation" in cleaned
    assert 'primary_key = "id"' in cleaned
    assert "/usr/bin" in cleaned


def test_scrub_removes_a_private_key_block(logd):
    text = "-----BEGIN OPENSSH PRIVATE KEY-----\nAAAA\n-----END OPENSSH PRIVATE KEY-----\n"
    cleaned = logd.scrub_line(text, {})
    assert "AAAA" not in cleaned
    assert "redacted" in cleaned


def test_flag_requires_a_regular_unwritable_root_owned_file(logd, tmp_path):
    missing = tmp_path / "missing"
    assert logd.flag_enabled(missing, root_uid=os.getuid()) is False
    flag = tmp_path / "enable-runner-logd"
    _flag(flag, 0o644)
    assert logd.flag_enabled(flag, root_uid=os.getuid()) is True
    _flag(flag, 0o444)
    assert logd.flag_enabled(flag, root_uid=os.getuid()) is True
    for mode in (0o664, 0o646, 0o666, 0o777):
        _flag(flag, mode)
        assert logd.flag_enabled(flag, root_uid=os.getuid()) is False, oct(mode)
    _flag(flag, 0o644)
    assert logd.flag_enabled(flag, root_uid=0) is False
    link = tmp_path / "linked"
    link.symlink_to(flag)
    assert logd.flag_enabled(link, root_uid=os.getuid()) is False
    directory = tmp_path / "dir"
    directory.mkdir()
    assert logd.flag_enabled(directory, root_uid=os.getuid()) is False


def test_flag_status_cli_ignores_a_non_root_override_when_root(logd, tmp_path, monkeypatch):
    flag = tmp_path / "enable-runner-logd"
    _flag(flag, 0o644)
    monkeypatch.setenv("RADON_RUNNER_LOGD_EXPECT_UID", str(os.getuid()))
    if os.geteuid() == 0:
        assert logd.flag_enabled(flag) is False
    else:
        assert logd.flag_enabled(flag) is True
    out = subprocess.run(
        [sys.executable, "-I", str(LOGD), "flag-status", "--flag", str(flag)],
        capture_output=True, text=True, check=True,
        env={**os.environ, "RADON_RUNNER_LOGD_EXPECT_UID": str(os.getuid())},
    )
    assert out.stdout.strip() == ("disabled" if os.geteuid() == 0 else "enabled")


def test_ingest_stores_a_scrubbed_line_and_drops_a_bad_loop(logd, tmp_path):
    root = tmp_path / "runner-logs"
    (root / "repo").mkdir(parents=True)
    logd.ingest_payload(f"security\tGH_TOKEN={SECRET}\n".encode(), root)
    stored = list((root / "repo" / "logs").rglob("*.log"))
    assert len(stored) == 1
    assert SECRET not in stored[0].read_text()
    assert "[redacted]" in stored[0].read_text()
    assert stat.S_IMODE(stored[0].stat().st_mode) == 0o600
    before = list((root / "repo" / "logs").rglob("*"))
    logd.ingest_payload(b"../etc\tnope\n", root)
    logd.ingest_payload(b"Security\tnope\n", root)
    assert list((root / "repo" / "logs").rglob("*")) == before


def test_push_refuses_a_readable_key_a_bad_repo_and_a_foreign_remote(logd, tmp_path):
    root = _store(tmp_path)
    calls = []

    def run(cmd, env):
        calls.append((cmd, env))
        if "get-url" in cmd:
            return 0, "git@github.com:other/name.git\n"
        return 0, ""

    assert logd.push_repo(root, "joemccann/radon-runner-logs", run=run) == "skipped"
    assert not any("push" in cmd for cmd, _env in calls)

    calls.clear()
    os.chmod(root / "keys" / "id_ed25519", 0o640)
    assert logd.push_repo(root, "joemccann/radon-runner-logs", run=run) == "skipped"
    assert calls == []
    os.chmod(root / "keys" / "id_ed25519", 0o600)
    assert logd.push_repo(root, "../radon-runner-logs", run=run) == "skipped"
    assert calls == []


def test_push_uses_the_private_key_and_drops_parent_secrets(logd, tmp_path, monkeypatch):
    root = _store(tmp_path)
    monkeypatch.setenv("GH_TOKEN", SECRET)
    monkeypatch.setenv("PUSHOVER_TOKEN", "pushover-token-value")
    calls = []

    def run(cmd, env):
        calls.append((cmd, dict(env)))
        if "get-url" in cmd:
            return 1, ""
        if "diff" in cmd and "--quiet" in cmd:
            return 1, ""
        return 0, ""

    assert logd.push_repo(root, "joemccann/radon-runner-logs", run=run) == "pushed"
    env = calls[0][1]
    assert "GH_TOKEN" not in env and "PUSHOVER_TOKEN" not in env
    assert SECRET not in env["GIT_SSH_COMMAND"]
    assert "IdentitiesOnly=yes" in env["GIT_SSH_COMMAND"]
    assert "IdentityAgent=none" in env["GIT_SSH_COMMAND"]
    assert str(root / "keys" / "id_ed25519") in env["GIT_SSH_COMMAND"]
    added = [cmd for cmd, _env in calls if "remote" in cmd and "add" in cmd][0]
    assert "git@github.com:joemccann/radon-runner-logs.git" in added
    assert not any(re.search(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", " ".join(cmd)) for cmd, _env in calls)


def test_socket_is_group_writable_only_and_not_a_symlink(logd, tmp_path):
    # AF_UNIX paths are capped near 104 bytes on macOS; pytest tmp_path is too long.
    root = Path(tempfile.mkdtemp(prefix="rl", dir="/tmp"))
    path = root / "s"
    try:
        sock = logd.bind_ingest_socket(path)
        try:
            assert stat.S_ISSOCK(path.lstat().st_mode)
            assert stat.S_IMODE(path.stat().st_mode) & 0o007 == 0
            assert stat.S_IMODE(path.stat().st_mode) & 0o020
        finally:
            sock.close()
        path.unlink()
        outside = tmp_path / "elsewhere.sock"
        outside.touch()
        link = root / "s"
        link.symlink_to(outside)
        with pytest.raises(OSError):
            logd.bind_ingest_socket(link)
        assert outside.read_text() == ""
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_install_creates_nothing_without_a_root_owned_flag(tmp_path):
    prefix = tmp_path / "prefix"
    prefix.mkdir()
    root = tmp_path / "runner-logs"
    env = {
        **os.environ,
        "RADON_RUNNER_PREFIX": str(prefix),
        "RADON_RUNNER_LOGD_ROOT": str(root),
        "RADON_RUNNER_PYTHON": sys.executable,
        "RADON_RUNNER_LOGD_EXPECT_UID": str(os.getuid()),
    }
    out = subprocess.run([BASH, str(INSTALL), "--install-logd"], capture_output=True, text=True, env=env)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "runner-logd disabled"
    assert not root.exists()
    assert "ssh-keygen" not in out.stderr
    assert "dscl" not in out.stderr

    flag = prefix / "enable-runner-logd"
    flag.write_text("on\n")
    os.chmod(flag, 0o666)
    out = subprocess.run([BASH, str(INSTALL), "--install-logd"], capture_output=True, text=True, env=env)
    assert out.returncode == 0, out.stderr
    assert "runner-logd disabled" in out.stdout
    assert not root.exists()

    link = tmp_path / "flag-link"
    real = tmp_path / "real-flag"
    real.write_text("on\n")
    os.chmod(real, 0o644)
    link.symlink_to(real)
    env["RADON_RUNNER_LOGD_FLAG"] = str(link)
    out = subprocess.run([BASH, str(INSTALL), "--install-logd"], capture_output=True, text=True, env=env)
    assert "runner-logd disabled" in out.stdout
    assert not root.exists()


def test_install_reaches_sudo_only_after_an_enabled_flag_and_creates_nothing(tmp_path):
    prefix = tmp_path / "prefix"
    prefix.mkdir()
    flag = prefix / "enable-runner-logd"
    flag.write_text("on\n")
    os.chmod(flag, 0o644)
    root = tmp_path / "runner-logs"
    env = {
        **os.environ,
        "RADON_RUNNER_PREFIX": str(prefix),
        "RADON_RUNNER_LOGD_ROOT": str(root),
        "RADON_RUNNER_PYTHON": sys.executable,
        "RADON_RUNNER_LOGD_EXPECT_UID": str(os.getuid()),
    }
    out = subprocess.run([BASH, str(INSTALL), "--install-logd"], capture_output=True, text=True, env=env)
    if os.geteuid() == 0:
        pytest.skip("root can pass the sudo gate on this host")
    assert out.returncode != 0
    assert "run with sudo" in out.stderr
    assert "runner-logd disabled" not in out.stdout
    assert not root.exists()
    assert not (prefix / "runner-logd.conf").exists()


def test_logd_install_is_gated_before_any_account_key_or_daemon():
    src = INSTALL.read_text()
    body = src.split("install_log_daemon() {", 1)[1].split("\n}\n", 1)[0]
    assert body.index("flag-status") < body.index("ensure_logd_user")
    assert body.index("ensure_logd_user") < body.index("ssh-keygen")
    assert body.index("ssh-keygen") < body.index("launchctl bootstrap")
    assert src.count("ensure_logd_user") == 2
    assert "install_log_daemon" in src.split("main() {", 1)[1]
    runner = src.split("install_runner() {", 1)[1].split("\n}\n", 1)[0]
    assert "ensure_logd_user" not in runner
    assert "enable-runner-logd" not in runner
    assert "> \"$LOGD_FLAG\"" not in src
    assert ">> \"$LOGD_FLAG\"" not in src


def test_logd_key_paths_refuse_a_symlink_and_root_only_touches_a_fresh_key():
    # install.sh re-runs as root every refresh. The keys/ directory belongs
    # to the daemon account, so a chown/chmod by path name there must only
    # ever land on a key root just generated, never on a planted link.
    src = INSTALL.read_text()
    body = src.split("install_log_daemon() {", 1)[1].split("\n}\n", 1)[0]
    keygen = body.index("ssh-keygen")
    for leaf in ("keys/id_ed25519\"", "keys/id_ed25519.pub\""):
        refusal = body.index(f'[[ ! -L "$LOGD_ROOT/{leaf}')
        assert refusal < keygen
    block = body[body.index("if [[ ! -e \"$LOGD_ROOT/keys/id_ed25519\" ]]"):]
    block = block[: block.index("\n  fi\n")]
    assert "ssh-keygen" in block
    assert block.count("chown") == 1 and block.count("chmod") == 2
    after = body[keygen:].split("\n  fi\n", 1)[1]
    for line in after.splitlines():
        if "id_ed25519" in line:
            assert "chown" not in line and "chmod" not in line


def test_follow_drops_the_body_of_a_key_block_split_across_lines(logd):
    # follow() scrubs one readline fragment at a time. A key printed as
    # separate lines never shows BEGIN and END to the same scrub call, so
    # the block state must live across lines.
    scrubber = logd.LineScrubber({})
    marker = "-----{} OPENSSH PRIVATE KEY-----"
    out = [scrubber.feed(line) for line in (
        "before",
        marker.format("BEGIN"),
        "synthetic-body-line-one",
        "synthetic-body-line-two",
        marker.format("END"),
        "after",
    )]
    joined = "\n".join(line for line in out if line is not None)
    assert "synthetic-body" not in joined
    assert "before" in joined and "after" in joined
    assert "redacted" in joined
    assert scrubber.feed("GH_TOKEN=ghp_abcdefghijklmnopqrstuvwxyz0123456789") == "GH_TOKEN=[redacted]"


def test_service_owned_children_refuse_a_symlink_before_root_installs_them():
    # install -d by root follows a link planted by the service account that
    # owns the parent (keys/ repo/ under the log daemon, secrets/ log/ tmp/
    # run/ under the publisher). Each child is refused as a link first.
    src = INSTALL.read_text()
    assert "refuse_link() {" in src
    helper = src.split("refuse_link() {", 1)[1].split("\n}\n", 1)[0]
    assert "-L" in helper
    logd_body = src.split("install_log_daemon() {", 1)[1].split("\n}\n", 1)[0]
    pub_body = src.split("install_publisher() {", 1)[1].split("\n}\n", 1)[0]
    for body, owner, children in (
        (logd_body, "$LOGD_USER", ('"$LOGD_ROOT/keys"', '"$LOGD_ROOT/repo"')),
        (pub_body, "$PUB_USER", ('"$PUB_ROOT/secrets"', '"$PUB_ROOT/log"', '"$PUB_ROOT/tmp"', '"$PUB_ROOT/run"')),
    ):
        guard = body.index("refuse_link ")
        first_install = body.index(f'install -d -o "{owner}"')
        assert guard < first_install
        guard_line = body[guard:].split("\n", 1)[0]
        for child in children:
            assert child in guard_line
        assert "|| return 77" in guard_line


def test_logd_plist_runs_as_its_own_account_without_secrets():
    out = subprocess.run([BASH, str(INSTALL), "--print-logd-plist"], capture_output=True, check=True)
    plist = plistlib.loads(out.stdout)
    assert plist["Label"] == "com.radon.runner-logd"
    assert plist["UserName"] == "_radonlog"
    assert plist["ProgramArguments"][-4:] == [
        "--root", "/Library/Radon/runner-logs",
        "--config", "/usr/local/radon-runner/runner-logd.conf",
    ]
    assert any(arg.endswith("runner_logd.py") for arg in plist["ProgramArguments"])
    assert "serve" in plist["ProgramArguments"]
    assert plist["RunAtLoad"] is True and plist["KeepAlive"] is True
    assert "EnvironmentVariables" not in plist
    assert b"GH_TOKEN" not in out.stdout and b"BEGIN " not in out.stdout


def test_public_logd_text_has_no_address_or_key_material():
    src = INSTALL.read_text()
    section = DOC.read_text().split("## External run logs", 1)[1].split("\n## ", 1)[0]
    follow = (ROOT / "scripts" / "runner" / "run_loop.sh").read_text().split("start_log_follow() {", 1)[1].split("\n}\n", 1)[0]
    blob = "\n".join((
        LOGD.read_text(),
        section,
        src.split("install_log_daemon() {", 1)[1].split("\n}\n", 1)[0],
        follow,
    ))
    assert not re.search(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", blob)
    assert "BEGIN OPENSSH PRIVATE KEY" not in blob
    assert "BEGIN PRIVATE KEY" not in blob
    for line in section.splitlines():
        assert not ("sudo" in line and "/tmp/" in line)


def _store(tmp_path: Path) -> Path:
    root = tmp_path / "runner-logs"
    keys = root / "keys"
    logs = root / "repo" / "logs" / "security"
    logs.mkdir(parents=True)
    keys.mkdir()
    key = keys / "id_ed25519"
    key.write_text("synthetic-private-key\n")
    os.chmod(key, 0o600)
    known = keys / "known_hosts"
    known.write_text("forge-pin\n")
    os.chmod(known, 0o600)
    (logs / "2026-01-01.log").write_text("plain line\n")
    return root


def test_ingest_keeps_an_embedded_line_break_from_forging_a_second_line(logd, tmp_path):
    # The store is append-only evidence of what the agent printed. A payload
    # carrying its own line breaks must land as ONE line, not as extra
    # timestamped entries nobody emitted.
    root = tmp_path / "runner-logs"
    (root / "repo").mkdir(parents=True)
    logd.ingest_payload(b"security\tfirst\n2026-01-01T00:00:00Z forged\r\nmore\n", root)
    stored = list((root / "repo" / "logs").rglob("*.log"))
    assert len(stored) == 1
    lines = stored[0].read_text().splitlines()
    assert len(lines) == 1
    assert "first" in lines[0] and "forged" in lines[0] and "more" in lines[0]
