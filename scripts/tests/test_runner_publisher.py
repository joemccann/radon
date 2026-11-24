"""The credential publisher is off unless a root-owned flag says otherwise.

Request checks refuse merges, force-pushes, and a branch outside the
caller's prefix. The install path creates nothing and copies no
credential while the flag is absent or untrusted.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import stat
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import pytest


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_RUNNER = Path(__file__).resolve().parents[2] / "scripts" / "runner"
publisher = _load("radon_publisher", _RUNNER / "publisher.py")
publisher_client = _load("radon_publisher_client", _RUNNER / "publisher_client.py")


REPO = Path(__file__).resolve().parents[2]
INSTALL = REPO / "scripts" / "runner" / "install.sh"
BRANCH = "security/2026-11-21"


class FakeOps:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def push(self, caller: str, branch: str, bundle: str) -> dict:
        self.calls.append(("push", caller, branch, bundle))
        return {"pushed": branch}

    def open_draft(self, caller: str, branch: str, title: str, body: str) -> dict:
        self.calls.append(("open_draft", caller, branch, title))
        return {"url": "https://github.com/joemccann/radon/pull/1"}

    def edit_draft(self, caller: str, branch: str, pr: str, title: str, body: str) -> dict:
        self.calls.append(("edit_draft", pr, branch))
        return {"url": f"https://github.com/joemccann/radon/pull/{pr}"}

    def publish_report(self, caller: str, phase: str, day: str, path: str) -> dict:
        self.calls.append(("publish_report", caller, phase, day, path))
        return {"report": "https://github.com/joemccann/radon-security-reports/blob/main/x"}

    def notify(self, caller: str, title: str, message: str, url: str, url_title: str) -> dict:
        self.calls.append(("notify", caller, title, url))
        return {"notified": True}


def _plant() -> str:
    return "PLANTED" + "SECRET" + "VALUE" + "1234"


def _cfg(tmp_path: Path, *, uid: int | None = None) -> publisher.Config:
    home = tmp_path / "Users" / "_radonbot"
    (home / "radon-runner" / "work" / "security").mkdir(parents=True)
    (home / "radon-runner" / "state" / "security" / "scratch").mkdir(parents=True)
    (home / "radon-runner" / "state" / "documentation" / "scratch").mkdir(parents=True)
    log = tmp_path / "log" / "requests.log"
    log.parent.mkdir()
    return publisher.Config(
        client_uid=os.getuid() if uid is None else uid,
        bot_home=home,
        secrets=tmp_path / "secrets",
        socket_path=tmp_path / "run" / "publisher.sock",
        log_path=log,
        tmp=tmp_path / "tmp",
        gitconfig=tmp_path / "gitconfig",
    )


def _owned(path: Path, text: str = "report\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(0o644)
    return path


def _reject(payload: dict) -> str:
    with pytest.raises(publisher.Rejected) as caught:
        publisher.validate(payload)
    return caught.value.code


def test_rejects_merge() -> None:
    assert _reject({"op": "merge", "caller": "security", "branch": BRANCH}) == "merge"
    assert (
        _reject(
            {
                "op": "draft_pr",
                "action": "merge",
                "caller": "security",
                "branch": BRANCH,
                "title": "t",
                "body": "b",
            }
        )
        == "merge"
    )


def test_rejects_force_push() -> None:
    base = {"op": "push", "caller": "security", "branch": BRANCH, "bundle": "/tmp/bundle"}
    assert _reject({**base, "force": True}) == "force_push"
    assert _reject({**base, "branch": "+" + BRANCH}) == "force_push"
    commands = publisher.push_commands("/usr/bin/git", BRANCH, "/tmp/in.bundle", "/tmp/repo.git")
    flat = [arg for argv in commands for arg in argv]
    assert "--force" not in flat
    assert "--force-with-lease" not in flat
    assert all(not arg.startswith("+") for arg in flat)
    assert "refs/heads/security/2026-11-21:refs/heads/security/2026-11-21" in flat
    assert publisher.RADON_REMOTE in flat
    report_push = publisher.report_push_command("/usr/bin/git", "/tmp/repo")
    assert "--force" not in report_push
    assert "HEAD:main" in report_push
    assert "+HEAD" not in " ".join(report_push)


def test_rejects_foreign_prefix() -> None:
    assert (
        _reject(
            {
                "op": "push",
                "caller": "security",
                "branch": "documentation/2026-11-21",
                "bundle": "/tmp/bundle",
            }
        )
        == "branch"
    )
    assert (
        _reject(
            {
                "op": "push",
                "caller": "security",
                "branch": "security/not-a-date",
                "bundle": "/tmp/bundle",
            }
        )
        == "branch"
    )
    assert (
        _reject(
            {
                "op": "push",
                "caller": "security",
                "branch": "security/2026-02-31",
                "bundle": "/tmp/bundle",
            }
        )
        == "date"
    )


def test_rejects_arbitrary_git_and_api() -> None:
    assert _reject({"op": "git", "argv": ["push", "--force"]}) == "command"
    assert _reject({"op": "api", "path": "/repos/joemccann/radon/pulls/1/merge"}) == "command"
    assert _reject({"op": "shell", "command": "id"}) == "command"


def test_draft_stays_draft_and_notify_url_is_bounded() -> None:
    opened = publisher.validate(
        {
            "op": "draft_pr",
            "action": "open",
            "caller": "security",
            "branch": BRANCH,
            "title": "Nightly",
            "body": "body",
        }
    )
    assert opened["action"] == "open"
    assert "pr" not in opened
    assert (
        _reject(
            {
                "op": "notify",
                "caller": "security",
                "title": "t",
                "message": "m",
                "url": "https://example.test/hook",
                "url_title": "x",
            }
        )
        == "url"
    )


def test_publish_report_is_security_loops_only() -> None:
    assert (
        _reject(
            {
                "op": "publish_report",
                "caller": "documentation",
                "phase": "audit",
                "date": "2026-11-21",
                "path": "/tmp/report.md",
            }
        )
        == "caller"
    )


def test_confined_read_rejects_escape(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    scratch = cfg.scratch_root("security")
    report = _owned(scratch / "latest.md", "phase report\n")
    assert publisher.read_confined([scratch], report, os.getuid(), 1000) == b"phase report\n"
    outside = _owned(tmp_path / "secrets" / "github-token", "nope\n")
    with pytest.raises(publisher.Rejected) as caught:
        publisher.read_confined([scratch], outside, os.getuid(), 1000)
    assert caught.value.code == "path"
    link = scratch / "via-link.md"
    link.symlink_to(outside)
    with pytest.raises(publisher.Rejected) as linked:
        publisher.read_confined([scratch], link, os.getuid(), 1000)
    assert linked.value.code == "path"
    with pytest.raises(publisher.Rejected):
        publisher.read_confined([scratch], report, os.getuid() + 1, 1000)


def test_dispatch_does_not_call_ops_for_a_foreign_or_escaping_request(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    ops = FakeOps()
    with pytest.raises(publisher.Rejected):
        publisher.dispatch(
            {
                "op": "push",
                "caller": "security",
                "branch": "documentation/2026-11-21",
                "bundle": "/tmp/bundle",
            },
            cfg,
            ops,
        )
    outside = _owned(tmp_path / "elsewhere" / "bundle", "bundle\n")
    with pytest.raises(publisher.Rejected):
        publisher.dispatch(
            {
                "op": "push",
                "caller": "security",
                "branch": BRANCH,
                "bundle": str(outside),
            },
            cfg,
            ops,
        )
    assert ops.calls == []


def test_dispatch_accepts_the_callers_own_branch(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    bundle = _owned(cfg.work_root("security") / "branch.bundle", "bundle\n")
    ops = FakeOps()
    result = publisher.dispatch(
        {
            "op": "push",
            "caller": "security",
            "branch": BRANCH,
            "bundle": str(bundle),
        },
        cfg,
        ops,
    )
    assert result["pushed"] == BRANCH
    assert ops.calls[0][0] == "push"
    assert ops.calls[0][2] == BRANCH


def test_every_request_is_logged_without_the_planted_secret(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    secret = _plant()
    ops = FakeOps()
    response = publisher.handle_payload(
        json.dumps(
            {
                "op": "merge",
                "caller": "security",
                "branch": BRANCH,
                "body": secret,
                "token": secret,
            }
        ).encode(),
        peer=cfg.client_uid,
        cfg=cfg,
        ops=ops,
    )
    assert response == {"ok": False, "error": "merge"}
    assert ops.calls == []
    text = cfg.log_path.read_text(encoding="utf-8")
    assert "result=rejected" in text
    assert "reason=merge" in text
    assert "op=merge" in text
    assert secret not in text
    assert stat.S_IMODE(cfg.log_path.stat().st_mode) == 0o600
    peer = publisher.handle_payload(b"{}", peer=cfg.client_uid + 1, cfg=cfg, ops=ops)
    assert peer["error"] == "peer"
    assert "reason=peer" in cfg.log_path.read_text(encoding="utf-8")


def _call(api: publisher_client.PublisherClient, payload: dict) -> dict:
    last: publisher_client.PublisherError | None = None
    for _ in range(40):
        try:
            return api.call(payload)
        except publisher_client.PublisherError as exc:
            last = exc
            if exc.code != "unavailable":
                raise
            time.sleep(0.025)
    assert last is not None
    raise last


def _short_socket(cfg: publisher.Config) -> Path:
    # AF_UNIX paths are capped near 104 bytes on macOS; pytest tmp_path is too long.
    root = Path(tempfile.mkdtemp(prefix="rp", dir="/tmp"))
    cfg.socket_path = root / "s"
    return root


def test_socket_rejects_force_push_and_records_nothing(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    root = _short_socket(cfg)
    ops = FakeOps()
    thread = threading.Thread(target=publisher.serve_once, args=(cfg, ops), daemon=True)
    thread.start()
    try:
        api = publisher_client.PublisherClient(str(cfg.socket_path), timeout=5)
        with pytest.raises(publisher_client.PublisherError) as caught:
            _call(api, {"op": "push", "caller": "security", "branch": BRANCH, "bundle": "/tmp/x", "force": True})
        thread.join(2)
        assert caught.value.code == "force_push"
        assert ops.calls == []
        assert "reason=force_push" in cfg.log_path.read_text(encoding="utf-8")
        assert stat.S_IMODE(cfg.socket_path.stat().st_mode) == 0o660
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_client_round_trip(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    root = _short_socket(cfg)
    bundle = _owned(cfg.work_root("security") / "branch.bundle", "bundle\n")
    ops = FakeOps()
    thread = threading.Thread(target=publisher.serve_once, args=(cfg, ops), daemon=True)
    thread.start()
    try:
        api = publisher_client.PublisherClient(str(cfg.socket_path), timeout=5)
        response = _call(api, {"op": "push", "caller": "security", "branch": BRANCH, "bundle": str(bundle)})
        thread.join(2)
        assert response["ok"] is True
        assert response["pushed"] == BRANCH
        assert publisher_client.DEFAULT_SOCKET == publisher.DEFAULT_SOCKET
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_child_env_drops_caller_credentials(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GH_TOKEN", _plant())
    monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/agent.sock")
    monkeypatch.setenv("https_proxy", "http://proxy.example")
    env = publisher.child_env(_cfg(tmp_path))
    assert "GH_TOKEN" not in env
    assert "SSH_AUTH_SOCK" not in env
    assert "https_proxy" not in env
    assert env["GIT_CONFIG_NOSYSTEM"] == "1"


def test_notify_argv_has_no_token(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    cfg.secrets.mkdir()
    token = "T" + "ok" + "en" + "1234567"
    user = "U" + "ser" + "1234567"
    (cfg.secrets / "pushover-token").write_text(token + "\n", encoding="utf-8")
    (cfg.secrets / "pushover-user").write_text(user + "\n", encoding="utf-8")
    os.chmod(cfg.secrets / "pushover-token", 0o600)
    os.chmod(cfg.secrets / "pushover-user", 0o600)
    seen: list[tuple[list[str], str | None]] = []

    def run(argv, **kwargs):
        seen.append((argv, kwargs.get("input")))
        return subprocess.CompletedProcess(argv, 0, stdout="200", stderr="")

    publisher.RealOps(cfg, run=run).notify("security", "title", "message", "", "")
    argv, stdin = seen[0]
    assert argv[-1] == publisher.PUSHOVER_URL
    assert token not in argv
    assert user not in argv
    assert stdin is not None and token in stdin and user in stdin


def test_shell_path_rejects_metacharacters() -> None:
    assert publisher.shell_path(Path("/Library/Radon/publisher/secrets/reports-deploy-key")).endswith("reports-deploy-key")
    with pytest.raises(publisher.Rejected):
        publisher.shell_path(Path("/tmp/key;id"))


def test_secret_line_rejects_curl_metacharacters(tmp_path: Path) -> None:
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    path = secrets / "github-token"
    path.write_text('abcd"efghijk\n', encoding="utf-8")
    path.chmod(0o600)
    with pytest.raises(publisher.Rejected) as caught:
        publisher._read_line(path, os.getuid(), secrets)
    assert caught.value.code == "secret_value"


def test_credential_helper_refuses_other_hosts(tmp_path: Path) -> None:
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    token = "G" + "it" + "Hub" + "1234567"
    (secrets / "github-token").write_text(token + "\n", encoding="utf-8")
    os.chmod(secrets / "github-token", 0o600)
    filled = publisher.credential_fill("protocol=https\nhost=github.com\n\n", secrets)
    assert "username=x-access-token" in filled
    assert token in filled
    assert publisher.credential_fill("protocol=https\nhost=example.test\n\n", secrets) == ""


def test_flag_missing_symlink_and_untrusted_owner_stay_off(tmp_path: Path) -> None:
    flag = tmp_path / "enable-publisher"
    assert publisher.flag_enabled(flag) == (False, "missing")
    target = tmp_path / "note"
    target.write_text("x", encoding="utf-8")
    flag.symlink_to(target)
    assert publisher.flag_enabled(flag)[1] == "symlink"
    flag.unlink()
    flag.write_text("on", encoding="utf-8")
    flag.chmod(0o644)
    assert publisher.flag_enabled(flag) == (False, "owner")
    flag.chmod(0o666)
    assert publisher.flag_enabled(flag)[0] is False
    enabled = publisher.FlagStat(
        exists=True,
        is_symlink=False,
        is_reg=True,
        uid=0,
        mode=0o644,
        parent_uid=0,
        parent_mode=0o755,
        parent_is_symlink=False,
    )
    assert publisher.decide_flag(enabled) == (True, "ok")
    writable = publisher.FlagStat(
        exists=True,
        is_symlink=False,
        is_reg=True,
        uid=0,
        mode=0o646,
        parent_uid=0,
        parent_mode=0o755,
        parent_is_symlink=False,
    )
    assert publisher.decide_flag(writable) == (False, "writable")
    parent = publisher.FlagStat(
        exists=True,
        is_symlink=False,
        is_reg=True,
        uid=0,
        mode=0o644,
        parent_uid=0,
        parent_mode=0o777,
        parent_is_symlink=False,
    )
    assert publisher.decide_flag(parent) == (False, "parent")


def test_gate_cli_and_install_plan_create_nothing(tmp_path: Path) -> None:
    prefix = tmp_path / "prefix"
    prefix.mkdir()
    root = tmp_path / "publisher-root"
    env = {
        **os.environ,
        "RADON_RUNNER_PREFIX": str(prefix),
        "RADON_RUNNER_PYTHON": "python3",
        "RADON_PUBLISHER_ROOT": str(root),
    }
    plan = subprocess.run(
        ["bash", str(INSTALL), "--publisher-plan"],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert plan.returncode == 0, plan.stderr
    assert plan.stdout.strip() == "publisher: disabled"
    assert not root.exists()
    flag = prefix / "enable-publisher"
    flag.write_text("on", encoding="utf-8")
    flag.chmod(0o666)
    again = subprocess.run(
        ["bash", str(INSTALL), "--publisher-plan"],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert again.stdout.strip() == "publisher: disabled"
    assert "writable" in again.stderr or "owner" in again.stderr
    assert not root.exists()
    listed = subprocess.run(
        ["bash", str(INSTALL), "--print-publisher-plist"],
        env={**env, "RADON_PUBLISHER_CLIENT_UID": "501"},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert listed.returncode == 0, listed.stderr
    assert "<string>_radonpub</string>" in listed.stdout
    assert "com.radon.publisher" in listed.stdout
    assert "GH_TOKEN" not in listed.stdout
    assert "PUSHOVER" not in listed.stdout


def test_install_gates_publisher_before_any_account_or_credential_copy() -> None:
    src = INSTALL.read_text(encoding="utf-8")
    body = src.split("install_publisher() {", 1)[1].split("\n}\n", 1)[0]
    assert body.index("gate") < body.index("return 0")
    assert body.index("return 0") < body.index("ensure_publisher_user")
    assert "test -w" in body
    assert body.index("test -w") < body.index("ensure_publisher_user")
    for banned in (".radon-runner.env", ".radon-runner-reports-key", "GH_TOKEN", "PUSHOVER_TOKEN", "PUSHOVER_USER"):
        assert banned not in body
    user = src.split("ensure_publisher_user() {", 1)[1].split("\n}\n", 1)[0]
    for banned in (".radon-runner.env", ".radon-runner-reports-key", "GH_TOKEN", "PUSHOVER"):
        assert banned not in user
    main = src.split("main() {", 1)[1]
    assert main.index("[[ $EUID -eq 0 ]]") < main.index("install_publisher")
    daemon = (REPO / "scripts" / "runner" / "publisher.py").read_text(encoding="utf-8")
    assert ".radon-runner.env" not in daemon
    assert ".radon-runner-reports-key" not in daemon
    assert publisher.ENABLE_FLAG in src


def test_scrub_removes_an_assignment(tmp_path: Path) -> None:
    del tmp_path
    secret = "Z" + "value" + "998877"
    cleaned = publisher._scrub(f"note\nGH_TOKEN={secret}\n")
    assert secret not in cleaned


def test_draft_title_and_body_are_scrubbed_before_gh_sees_them(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # The draft body reaches a PUBLIC pull request. publish_report scrubs its
    # text; the draft path must pass through the same chokepoint.
    cfg = _cfg(tmp_path)
    cfg.tmp.mkdir()
    ops = publisher.RealOps(cfg)
    secret = _plant()
    seen: dict[str, object] = {}

    def fake_gh(args: list[str], stdin: str | None) -> subprocess.CompletedProcess[str]:
        seen["args"] = list(args)
        seen["body"] = Path(args[args.index("--body-file") + 1]).read_text(encoding="utf-8")
        return subprocess.CompletedProcess(args, 0, stdout="https://github.com/joemccann/radon/pull/7\n", stderr="")

    monkeypatch.setattr(ops, "_gh", fake_gh)
    out = ops.open_draft("security", BRANCH, f"Nightly GH_TOKEN={secret}", f"## Summary\nGH_TOKEN={secret}\n")
    assert out["url"].endswith("/pull/7")
    argv = " ".join(seen["args"])  # type: ignore[arg-type]
    assert secret not in argv and secret not in str(seen["body"])
    assert "Nightly" in argv and "## Summary" in str(seen["body"])


def test_an_idle_connection_does_not_wedge_the_serving_loop(tmp_path: Path) -> None:
    # One client that connects and sends nothing must not block every other
    # loop's push, draft and notify behind it.
    import socket

    cfg = _cfg(tmp_path)
    root = _short_socket(cfg)
    bundle = _owned(cfg.work_root("security") / "branch.bundle", "bundle\n")
    ops = FakeOps()
    before = os.umask(0)
    os.umask(before)
    thread = threading.Thread(target=publisher.serve, args=(cfg, ops), kwargs={"timeout": 0.5}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not cfg.socket_path.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    idle = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        idle.connect(str(cfg.socket_path))
        api = publisher_client.PublisherClient(str(cfg.socket_path), timeout=5)
        response = _call(api, {"op": "push", "caller": "security", "branch": BRANCH, "bundle": str(bundle)})
    finally:
        idle.close()
        os.umask(before)
        shutil.rmtree(root, ignore_errors=True)
    assert response["ok"] is True
    assert response["pushed"] == BRANCH
