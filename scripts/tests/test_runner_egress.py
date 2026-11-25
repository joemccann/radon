"""Off-by-default log-only egress proxy for the Mac runner.

The allowlist is a root-owned host file. install.sh must not create the
egress account, daemon, pf anchor, or pf.conf edit unless the enable flag
is a root-owned regular file that the runner account cannot write.
"""
from __future__ import annotations

import json
import os
import plistlib
import re
import socket
import stat
import subprocess
import threading
from pathlib import Path

import pytest

pytest_plugins = ["test_runner_run_loop"]

REPO = Path(__file__).resolve().parents[2]
RUNNER = REPO / "scripts" / "runner"
INSTALL = RUNNER / "install.sh"
PROXY = RUNNER / "egress_proxy.py"
REPORT = RUNNER / "egress_report.py"
EXAMPLE = RUNNER / "egress-allowlist.example"


def _load(name, path):
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_proxy():
    return _load("egress_proxy", PROXY)


def load_report():
    return _load("egress_report", REPORT)


def _stat(mode, uid=0, nlink=1):
    return os.stat_result((mode, 1, 1, nlink, uid, 0, 0, 0, 0, 0))


@pytest.fixture
def proxy():
    return load_proxy()


def test_example_allowlist_is_empty_and_has_no_network_literals(proxy):
    text = EXAMPLE.read_text(encoding="utf-8")
    parsed = proxy.parse_allowlist(text)
    assert parsed.rules == ()
    assert parsed.ignored_mode is None
    lowered = text.lower()
    assert "://" not in lowered
    assert not re.search(r"\d+\.\d+", text)
    assert not re.search(r":\d+", text)
    for token in ("example.com", "github", "localhost", "pushover", "*."):
        assert token not in lowered


def test_parse_allowlist_accepts_hosts_and_addresses(proxy):
    parsed = proxy.parse_allowlist(
        "\n".join(
            [
                "# comment",
                "",
                "mode log-only",
                "host Allowed.Invalid",
                "host other.invalid 443",
                "ipv4 192.0.2.10",
                "ipv4 192.0.2.11 22",
                "ipv6 2001:db8::10",
            ]
        )
    )
    assert parsed.ignored_mode is None
    assert parsed.matches("allowed.invalid", 8443)
    assert parsed.matches("other.invalid", 443)
    assert not parsed.matches("other.invalid", 80)
    assert parsed.matches("192.0.2.10", 443)
    assert parsed.matches("192.0.2.11", 22)
    assert not parsed.matches("192.0.2.11", 443)
    assert parsed.matches("2001:db8::10", 443)
    assert not parsed.matches("blocked.invalid", 443)


@pytest.mark.parametrize(
    "text",
    [
        "host *.invalid",
        "host https://allowed.invalid",
        "host allowed.invalid 0",
        "host allowed.invalid 65536",
        "ipv4 not-an-ip",
        "mode yolo",
        "permit allowed.invalid",
    ],
)
def test_parse_allowlist_rejects_widening_or_malformed_lines(proxy, text):
    with pytest.raises(ValueError):
        proxy.parse_allowlist(text)


def test_mode_block_is_ignored_and_does_not_change_matching(proxy):
    parsed = proxy.parse_allowlist("mode block\nhost allowed.invalid\n")
    assert parsed.ignored_mode == "block"
    assert parsed.matches("allowed.invalid", 443)
    assert not parsed.matches("blocked.invalid", 443)


def test_flag_verdict_requires_root_ownership_and_a_closed_parent_chain(proxy):
    parents = [_stat(stat.S_IFDIR | 0o755)]
    assert proxy.flag_verdict(_stat(stat.S_IFREG | 0o644), parents, bot_uid=501) == (True, "ok")
    assert proxy.flag_verdict(_stat(stat.S_IFREG | 0o644, uid=501), parents, bot_uid=501)[0] is False
    assert proxy.flag_verdict(_stat(stat.S_IFREG | 0o646), parents, bot_uid=501)[0] is False
    assert proxy.flag_verdict(_stat(stat.S_IFREG | 0o664), parents, bot_uid=501)[0] is False
    assert proxy.flag_verdict(_stat(stat.S_IFREG | 0o644, nlink=2), parents, bot_uid=501)[0] is False
    assert proxy.flag_verdict(_stat(stat.S_IFLNK | 0o777), parents, bot_uid=501)[0] is False
    writable_parent = [_stat(stat.S_IFDIR | 0o777)]
    assert proxy.flag_verdict(_stat(stat.S_IFREG | 0o644), writable_parent, bot_uid=501)[0] is False
    sticky = [_stat(stat.S_IFDIR | 0o1777)]
    assert proxy.flag_verdict(_stat(stat.S_IFREG | 0o644), sticky, bot_uid=501) == (True, "ok")
    assert proxy.flag_verdict(_stat(stat.S_IFREG | 0o644), [], bot_uid=501)[0] is False
    assert proxy.flag_verdict(_stat(stat.S_IFREG | 0o644), parents, bot_uid=0)[0] is False


def test_flag_status_rejects_missing_symlink_and_unprivileged_files(proxy, tmp_path):
    missing = tmp_path / "enable-egress"
    assert proxy.flag_status(missing)[0] is False
    target = tmp_path / "target"
    target.write_text("on\n")
    link = tmp_path / "link"
    link.symlink_to(target)
    assert proxy.flag_status(link) == (False, "symlink")
    assert proxy.flag_status(target)[0] is False


def test_install_is_a_noop_without_a_valid_flag(proxy, monkeypatch):
    applied = []
    monkeypatch.setattr(proxy, "flag_status", lambda path: (False, "missing"))
    monkeypatch.setattr(proxy, "apply_install", lambda: applied.append("applied"))
    assert proxy.cmd_install() == 0
    assert applied == []


def test_install_refuses_an_untrusted_allowlist_before_any_mutation(proxy, monkeypatch, tmp_path):
    applied = []
    allow = tmp_path / "egress-allowlist"
    allow.write_text("host allowed.invalid\n")
    flag = tmp_path / "enable-egress"
    original = proxy.flag_status
    monkeypatch.setattr(proxy, "ENABLE_FLAG", flag)
    monkeypatch.setattr(proxy, "ALLOWLIST_PATH", allow)

    def status(path):
        if path == flag:
            return True, "ok"
        return original(path)

    monkeypatch.setattr(proxy, "flag_status", status)
    monkeypatch.setattr(proxy, "apply_install", lambda: applied.append("applied"))
    monkeypatch.setattr(proxy.sys, "platform", "darwin")
    assert proxy.cmd_install() == 1
    assert applied == []


def test_install_does_not_apply_off_macos_even_when_the_flag_is_valid(proxy, monkeypatch):
    allow = Path("/usr/local/radon-runner/egress-allowlist-missing")
    monkeypatch.setattr(proxy, "ALLOWLIST_PATH", allow)
    monkeypatch.setattr(proxy, "apply_install", lambda: (_ for _ in ()).throw(AssertionError("applied")))
    monkeypatch.setattr(proxy.sys, "platform", "linux")

    def status(path):
        if path == allow:
            return False, "missing"
        return True, "ok"

    monkeypatch.setattr(proxy, "flag_status", status)
    assert proxy.cmd_install() == 1


def test_anchor_is_log_only(proxy):
    text = proxy.render_anchor()
    proxy.validate_anchor(text)
    active = [line.split("#", 1)[0].strip() for line in text.splitlines()]
    active = [line for line in active if line]
    assert active
    for line in active:
        head = line.split()[0]
        assert head == "pass"
        assert "quick" not in line.split()
    blob = "\n".join(active)
    assert "_radonbot" in blob and "_radonegress" in blob
    for forbidden in ("block", "drop", "rdr", "nat", "divert"):
        assert forbidden not in blob


def test_pf_conf_merge_is_idempotent_and_refuses_a_partial_marker(proxy):
    original = "scrub in all\npass out all\n"
    once = proxy.merge_pf_conf(original)
    twice = proxy.merge_pf_conf(once)
    assert twice == once
    assert once.count("# BEGIN com.radon.egress") == 1
    assert 'anchor "com.radon.egress"' in once
    assert "pass out all" in once
    with pytest.raises(ValueError):
        proxy.merge_pf_conf(original + "# BEGIN com.radon.egress\n")


def test_pf_reload_failure_restores_the_previous_conf(proxy, tmp_path):
    path = tmp_path / "pf.conf"
    path.write_text("pass out all\n")
    calls = []

    def run(argv):
        calls.append(list(argv))
        if argv[:2] == ["/sbin/pfctl", "-f"] and sum(1 for item in calls if item[:2] == ["/sbin/pfctl", "-f"]) == 1:
            return 1
        return 0

    with pytest.raises(RuntimeError):
        proxy.load_pf_conf(path, run)
    assert path.read_text() == "pass out all\n"
    assert ["/sbin/pfctl", "-e"] not in [item[:2] for item in calls]


def test_account_commands_do_not_grant_the_runner_account(proxy):
    commands = proxy.account_create_commands(410, 410, "user-uuid", "group-uuid", create_group=True)
    flat = " ".join(" ".join(cmd) for cmd in commands)
    assert "_radonbot" not in flat
    assert "dseditgroup" not in flat
    assert "UserShell /usr/bin/false" in flat
    assert "NFSHomeDirectory /var/empty" in flat
    assert "IsHidden 1" in flat
    pf = proxy.pf_load_commands(Path("/etc/pf.anchors/com.radon.egress"), Path("/etc/pf.conf"))
    assert all(cmd[:2] != ["/sbin/pfctl", "-e"] for cmd in pf)


def test_plist_runs_the_egress_account_and_fixed_arguments(proxy):
    plist = plistlib.loads(proxy.render_plist("/opt/homebrew/bin/python3.13", "/usr/local/radon-runner/egress_proxy.py"))
    assert plist["Label"] == "com.radon.egress-proxy"
    assert plist["UserName"] == "_radonegress"
    assert plist["ProgramArguments"] == [
        "/opt/homebrew/bin/python3.13",
        "-I",
        "/usr/local/radon-runner/egress_proxy.py",
        "serve",
    ]
    assert plist["RunAtLoad"] is True
    assert "/Users/_radonbot" not in " ".join(plist["ProgramArguments"])


def test_install_sh_gates_egress_and_does_not_edit_pf_itself():
    src = INSTALL.read_text(encoding="utf-8")
    assert "egress_proxy.py" in src
    assert "/etc/pf.conf" not in src
    assert "pfctl" not in src
    assert "_radonegress" not in src
    main = src.split("main() {", 1)[1]
    assert main.index("install_runner") < main.index("install_egress")
    body = src.split("install_egress() {", 1)[1].split("\n}\n", 1)[0]
    assert "egress_proxy.py" in body and "install" in body


def test_run_loop_arms_the_proxy_only_after_gate_and_probe():
    shell = (RUNNER / "run_loop.sh").read_text(encoding="utf-8")
    assert "arm_egress_proxy" in shell
    launch = shell.split("launch_agent() {", 1)[1].split("\n}\n", 1)[0]
    assert "arm_egress_proxy" in launch
    arm = shell.split("arm_egress_proxy() {", 1)[1].split("\n}\n", 1)[0]
    assert "gate" in arm and "probe" in arm
    assert str(load_proxy().LISTEN_PORT) in arm
    export_at = arm.index("export http_proxy")
    assert arm.index("gate") < export_at
    assert arm.index("probe") < export_at


def test_log_only_forwards_a_destination_that_would_be_blocked(proxy, tmp_path):
    upstream = socket.socket()
    upstream.bind(("localhost", 0))
    upstream.listen(1)
    port = upstream.getsockname()[1]
    seen = {}

    def serve_upstream():
        conn, _ = upstream.accept()
        conn.settimeout(2)
        data = b""
        while b"ping" not in data:
            chunk = conn.recv(1024)
            if not chunk:
                break
            data += chunk
        seen["data"] = data
        conn.sendall(b"ok")
        conn.close()

    thread = threading.Thread(target=serve_upstream, daemon=True)
    thread.start()
    log_path = tmp_path / "decisions.log"
    allow = proxy.parse_allowlist("host allowed.invalid\n")

    def resolve(host, dest_port):
        return [("localhost", port)]

    proxy_sock = socket.socket()
    proxy_sock.bind(("localhost", 0))
    proxy_sock.listen(1)
    proxy_port = proxy_sock.getsockname()[1]

    def accept_one():
        conn, _ = proxy_sock.accept()
        proxy.handle_connection(conn, allow, log_path, resolve, load_reason="ok", idle=2)

    accepter = threading.Thread(target=accept_one, daemon=True)
    accepter.start()
    client = socket.create_connection(("localhost", proxy_port), 2)
    client.settimeout(2)
    client.sendall(b"CONNECT blocked.invalid:%d HTTP/1.1\r\nHost: blocked.invalid:%d\r\n\r\n" % (port, port))
    buf = b""
    while b"\r\n\r\n" not in buf:
        buf += client.recv(1024)
    assert buf.startswith(b"HTTP/1.1 200")
    client.sendall(b"ping")
    assert client.recv(16) == b"ok"
    client.close()
    accepter.join(3)
    thread.join(3)
    assert seen["data"] == b"ping"
    rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert rows[-1]["decision"] == "would_block"
    assert rows[-1]["action"] == "forward"
    assert rows[-1]["host"] == "blocked.invalid"
    assert rows[-1]["reason"] == "not_allowlisted"
    report = load_report()
    summary = report.summarize(log_path.read_text(encoding="utf-8"))
    day = rows[-1]["day"]
    assert summary[day][f"blocked.invalid:{port}"] == 1
    upstream.close()
    proxy_sock.close()


def test_log_only_still_forwards_when_blocking_mode_was_requested(proxy, tmp_path):
    upstream = socket.socket()
    upstream.bind(("localhost", 0))
    upstream.listen(1)
    port = upstream.getsockname()[1]

    def serve_upstream():
        conn, _ = upstream.accept()
        conn.settimeout(2)
        conn.recv(16)
        conn.sendall(b"ok")
        conn.close()

    threading.Thread(target=serve_upstream, daemon=True).start()
    allow = proxy.parse_allowlist("mode block\nhost allowed.invalid\n")
    log_path = tmp_path / "decisions.log"
    proxy_sock = socket.socket()
    proxy_sock.bind(("localhost", 0))
    proxy_sock.listen(1)

    def accept_one():
        conn, _ = proxy_sock.accept()
        proxy.handle_connection(
            conn,
            allow,
            log_path,
            lambda host, dest_port: [("localhost", port)],
            load_reason="blocking_mode_ignored",
            idle=2,
        )

    threading.Thread(target=accept_one, daemon=True).start()
    client = socket.create_connection(("localhost", proxy_sock.getsockname()[1]), 2)
    client.settimeout(2)
    client.sendall(f"CONNECT blocked.invalid:{port} HTTP/1.1\r\nHost: blocked.invalid:{port}\r\n\r\n".encode())
    buf = b""
    while b"\r\n\r\n" not in buf:
        buf += client.recv(1024)
    assert buf.startswith(b"HTTP/1.1 200")
    client.sendall(b"ping")
    assert client.recv(8) == b"ok"
    client.close()
    upstream.close()
    proxy_sock.close()


def test_peer_policy_allows_root_self_and_the_runner_only(proxy, monkeypatch):
    monkeypatch.setattr(proxy.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(proxy, "_bot_uid", lambda: 501)
    monkeypatch.setattr(proxy, "peer_uid", lambda conn: 0)
    assert proxy.peer_allowed(None) is True
    monkeypatch.setattr(proxy, "peer_uid", lambda conn: 1000)
    assert proxy.peer_allowed(None) is True
    monkeypatch.setattr(proxy, "peer_uid", lambda conn: 501)
    assert proxy.peer_allowed(None) is True
    monkeypatch.setattr(proxy, "peer_uid", lambda conn: None)
    assert proxy.peer_allowed(None) is True
    monkeypatch.setattr(proxy, "peer_uid", lambda conn: -1)
    assert proxy.peer_allowed(None) is True
    monkeypatch.setattr(proxy, "peer_uid", lambda conn: 999)
    assert proxy.peer_allowed(None) is False


def test_a_foreign_local_peer_is_not_forwarded(proxy, monkeypatch, tmp_path):
    monkeypatch.setattr(proxy, "peer_allowed", lambda conn: False)
    upstream = socket.socket()
    upstream.bind(("localhost", 0))
    upstream.listen(1)
    accepted = []

    def serve_upstream():
        conn, _addr = upstream.accept()
        accepted.append(conn)

    threading.Thread(target=serve_upstream, daemon=True).start()
    log_path = tmp_path / "decisions.log"
    proxy_sock = socket.socket()
    proxy_sock.bind(("localhost", 0))
    proxy_sock.listen(1)

    def accept_one():
        conn, _addr = proxy_sock.accept()
        proxy.handle_connection(
            conn, proxy.parse_allowlist(""), log_path,
            lambda host, dest_port: [("localhost", upstream.getsockname()[1])],
            idle=1,
        )

    threading.Thread(target=accept_one, daemon=True).start()
    client = socket.create_connection(("localhost", proxy_sock.getsockname()[1]), 2)
    client.settimeout(2)
    client.sendall(b"CONNECT blocked.invalid:443 HTTP/1.1\r\nHost: blocked.invalid:443\r\n\r\n")
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = client.recv(1024)
        if not chunk:
            break
        buf += chunk
    assert buf.startswith(b"HTTP/1.1 403")
    assert accepted == []
    client.close()
    upstream.close()
    proxy_sock.close()


def test_untrusted_allowlist_does_not_permit_a_listed_host(proxy, tmp_path):
    path = tmp_path / "egress-allowlist"
    path.write_text("host allowed.invalid\n")
    allow, reason = proxy.load_allowlist(path)
    assert reason == "allowlist_untrusted"
    assert allow.matches("allowed.invalid", 443) is False


def test_report_groups_would_block_hits_by_day(tmp_path):
    report = load_report()
    log = tmp_path / "decisions.log"
    rows = [
        {"day": "2026-11-20", "decision": "would_block", "host": "blocked.invalid", "port": 443},
        {"day": "2026-11-20", "decision": "allow", "host": "allowed.invalid", "port": 443},
        {"day": "2026-11-20", "decision": "would_block", "host": "blocked.invalid", "port": 443},
        {"day": "2026-11-21", "decision": "would_block", "host": "other.invalid", "port": 443},
        "not json",
    ]
    log.write_text("".join((json.dumps(row) if not isinstance(row, str) else row) + "\n" for row in rows))
    text = report.render(report.summarize(log.read_text()))
    assert text == "2026-11-20\nblocked.invalid:443 2\n2026-11-21\nother.invalid:443 1\n"
    assert report.main(["--log", str(log)]) == 0
    assert report.render(report.summarize("")) == "no would-block hits\n"


def test_report_escapes_terminal_control_characters_in_destinations():
    # The CONNECT host is logged before validation, so a local client can
    # put any byte into decisions.log. The operator report shows it escaped.
    report = load_report()
    rows = [
        {"day": "2026-11-20", "decision": "would_block", "host": "evil\x1b[2J\x1b[Hclean.invalid", "port": 443},
        {"day": "2026-11-21\x07", "decision": "would_block", "host": "x.invalid", "port": 443},
    ]
    text = report.render(report.summarize("".join(json.dumps(row) + "\n" for row in rows)))
    assert "\x1b" not in text and "\x07" not in text
    assert "\\x1b[2J" in text and "\\x07" in text
    assert "clean.invalid:443 1" in text


def test_serve_loop_refuses_connections_past_the_cap_and_bounds_tunnel_idle(proxy, monkeypatch, tmp_path):
    monkeypatch.setattr(proxy, "DECISION_LOG", tmp_path / "decisions.log")
    server = socket.socket()
    server.bind(("localhost", 0))
    server.listen(8)
    server.settimeout(0.2)
    stop = threading.Event()
    held = []

    def hold(conn, allow, log_path, resolve, load_reason="ok", idle=None):
        held.append(idle)
        stop.wait(5)
        conn.close()

    monkeypatch.setattr(proxy, "handle_connection", hold)
    monkeypatch.setattr(proxy, "load_allowlist", lambda path: (proxy.parse_allowlist(""), "ok"))
    slots = threading.BoundedSemaphore(1)
    thread = threading.Thread(target=proxy.serve_loop, args=(server, stop, slots), daemon=True)
    thread.start()
    port = server.getsockname()[1]
    first = socket.create_connection(("localhost", port), 2)
    deadline = __import__("time").monotonic() + 3
    while not held and __import__("time").monotonic() < deadline:
        __import__("time").sleep(0.02)
    assert held == [proxy.TUNNEL_IDLE_SECS]
    second = socket.create_connection(("localhost", port), 2)
    second.settimeout(2)
    assert second.recv(1024).startswith(b"HTTP/1.1 503")
    assert second.recv(1024) == b""
    stop.set()
    thread.join(5)
    assert not thread.is_alive()
    first.close()
    second.close()
    server.close()
    assert proxy.MAX_CONNECTIONS >= 16
    logged = [json.loads(line) for line in (tmp_path / "decisions.log").read_text().splitlines()]
    assert [row["reason"] for row in logged] == ["busy"]


def test_relay_send_to_a_stalled_peer_is_bounded_by_the_idle_timeout(proxy):
    # relay() switched the destination to blocking before sendall, so a peer
    # that stops reading held the worker (and its connection slot) forever.
    import time

    client, relay_left = socket.socketpair()
    relay_right, upstream = socket.socketpair()
    for sock in (relay_right, upstream):
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 8192)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 8192)
    ended: dict[str, float] = {}

    def run() -> None:
        proxy.relay(relay_left, relay_right, 0.5)
        ended["at"] = time.monotonic()

    def send() -> None:
        try:
            client.sendall(b"x" * (4 * 1024 * 1024))
        except OSError:
            pass

    worker = threading.Thread(target=run, daemon=True)
    sender = threading.Thread(target=send, daemon=True)
    started = time.monotonic()
    worker.start()
    sender.start()
    worker.join(5)
    alive = worker.is_alive()
    for sock in (client, relay_left, relay_right, upstream):
        sock.close()
    sender.join(2)
    assert not alive
    assert ended["at"] - started < 4


def test_runner_does_not_export_the_proxy_when_the_flag_is_absent(rig):
    proc = rig.run(STUB_GROK="echo 'RESULT: https://x/pull/7 - fixed a stale runbook'; exit 0")
    assert proc.returncode == 0, proc.stderr + rig.log()
    env = (rig.calls / "grok.env").read_text()
    assert "8373" not in env
    assert "http_proxy=" not in env.lower()


def test_cli_plan_stays_disabled_for_a_user_owned_flag(tmp_path):
    flag = tmp_path / "enable-egress"
    flag.write_text("on\n")
    env = {**os.environ, "RADON_EGRESS_ENABLE_FLAG": str(flag)}
    out = subprocess.run(["python3", str(PROXY), "plan"], capture_output=True, text=True, env=env, check=False)
    # The production gate ignores environment overrides. A user-owned file at the
    # default path is not present here, so the plan stays disabled.
    assert out.returncode == 0
    assert out.stdout.strip().startswith("egress: disabled")
    assert "dscl" not in out.stdout
    assert "pfctl" not in out.stdout
