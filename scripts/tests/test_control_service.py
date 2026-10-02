"""Tests for the host control daemon (scripts/control_service, radon-control.service).

The daemon is the only path from the containerised radon-api to systemd. These
pin the security-relevant pieces: the unit/verb allowlist, the exact argv it
hands sudo, the peer-credential gate, the self-protection rules for units that
host the admin panel, and the deploy-lock refusal. A real AF_UNIX socket is
exercised end to end with the API-side client.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

import pytest

from control_service import serve

_REPO_ROOT = Path(__file__).resolve().parents[2]

LOADED = [
    "radon-api.service",
    "radon-nextjs.service",
    "radon-relay.service",
    "radon-monitor.service",
    "radon-health.service",
    "radon-control.service",
    "radon-cor.service",
    "radon-cor.timer",
]


class FakeRunner:
    """Records every argv the daemon would exec; never runs anything."""

    def __init__(self, rc: int = 0, out: str = "ok") -> None:
        self.calls: list[list[str]] = []
        self.rc = rc
        self.out = out

    def __call__(self, argv, timeout):
        self.calls.append(list(argv))
        return self.rc, self.out


def make_controller(tmp_path: Path, runner: FakeRunner | None = None, **kw) -> serve.Controller:
    runner = runner or FakeRunner()
    spawned: list = []
    controller = serve.Controller(
        runner=runner,
        loaded_units=lambda: list(LOADED),
        unit_rows=lambda: [{"Id": u, "ActiveState": "active"} for u in LOADED],
        deploy_lock_path=tmp_path / "deploy.lock",
        spawn=kw.pop("spawn", lambda fn: spawned.append(fn)),
        **kw,
    )
    controller._spawned = spawned  # type: ignore[attr-defined]
    return controller


class TestIsolation:
    def test_import_pulls_in_no_trading_stack(self):
        forbidden = {"ib_insync", "uvicorn", "fastapi", "starlette", "libsql",
                     "libsql_experimental", "ibapi", "eventkit", "httpx"}
        code = (
            "import sys; import control_service.serve;\n"
            "bad = sorted(m for m in sys.modules if m.split('.')[0] in %r);\n"
            "print(','.join(bad)); sys.exit(1 if bad else 0)" % (forbidden,)
        )
        env = {**os.environ, "PYTHONPATH": os.pathsep.join(["scripts", "."])}
        r = subprocess.run([sys.executable, "-c", code], capture_output=True,
                           text=True, env=env, cwd=_REPO_ROOT, timeout=30)
        assert r.returncode == 0, f"control daemon imported: {r.stdout.strip()} {r.stderr.strip()}"

    def test_module_entrypoint_imports_as_systemd_runs_it(self):
        # radon-control.service runs `python -m scripts.control_service.serve`
        # from /home/radon/radon with no PYTHONPATH.
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        r = subprocess.run(
            [sys.executable, "-c", "import scripts.control_service.serve as s; print(s.CONTROL_UNIT)"],
            capture_output=True, text=True, env=env, cwd=_REPO_ROOT, timeout=30,
        )
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip() == "radon-control.service"


class TestAllowlist:
    @pytest.mark.parametrize("unit", [
        "ssh.service",
        "radon-api",  # unsuffixed: the operator CLI requires the suffix
        "radon-api.service; rm -rf /",
        "radon-api.service\nradon-relay.service",
        "$(reboot).service",
        "radon-../etc.service",
        "RADON-api.service",
        "radon-api.socket",
        "radon-api.service --now",
        "",
        "radon-unknown.service",  # well-formed but not a loaded unit
        "radon-ib-gateway.service",
        "radon-ib-gateway-preheld-restart.service",
        "radon-ib-gateway-remote.service",
        "radon-beta-api.service",
        "radon-control.service",
    ])
    def test_unknown_or_dangerous_units_are_refused_without_exec(self, tmp_path, unit):
        runner = FakeRunner()
        controller = make_controller(tmp_path, runner)
        reply = controller.handle({"op": "unit", "verb": "restart", "unit": unit}, peer_uid=os.getuid())
        assert reply["ok"] is False
        assert reply["refused"] is True
        assert runner.calls == []

    @pytest.mark.parametrize("verb", ["enable", "disable", "kill", "mask", "reload", "", "restart;id", "RESTART"])
    def test_unknown_verbs_are_refused(self, tmp_path, verb):
        runner = FakeRunner()
        controller = make_controller(tmp_path, runner)
        reply = controller.handle({"op": "unit", "verb": verb, "unit": "radon-relay.service"}, peer_uid=os.getuid())
        assert reply["ok"] is False and reply["refused"] is True
        assert runner.calls == []

    @pytest.mark.parametrize("payload", [
        {"op": "shell", "cmd": "id"},
        {"op": "unit", "verb": "restart", "unit": "radon-relay.service", "extra": "x"},
        {"op": "unit", "verb": ["restart"], "unit": "radon-relay.service"},
        {"op": "unit", "verb": "restart", "unit": 7},
        {"op": "stack-restart", "unit": "radon-relay.service"},
    ])
    def test_malformed_requests_are_refused(self, tmp_path, payload):
        runner = FakeRunner()
        controller = make_controller(tmp_path, runner)
        reply = controller.handle(payload, peer_uid=os.getuid())
        assert reply["ok"] is False
        assert runner.calls == []

    def test_parse_request_rejects_non_object_and_oversized(self):
        with pytest.raises(ValueError):
            serve.parse_request(b"[1,2]")
        with pytest.raises(ValueError):
            serve.parse_request(b"not json")
        with pytest.raises(ValueError):
            serve.parse_request(b"{" + b" " * (serve.MAX_REQUEST_BYTES + 1) + b"}")


class TestArgv:
    def test_unit_action_execs_the_sudoers_granted_operator_argv(self, tmp_path):
        runner = FakeRunner(out="restart completed for radon-relay.service")
        controller = make_controller(tmp_path, runner)
        reply = controller.handle(
            {"op": "unit", "verb": "restart", "unit": "radon-relay.service", "actor": "user_abc"},
            peer_uid=os.getuid(),
        )
        assert reply["ok"] is True
        assert reply["returncode"] == 0
        assert runner.calls == [[
            "/usr/bin/sudo", "-n", "/usr/local/bin/radon", "unit", "restart", "radon-relay.service",
        ]]

    def test_timer_units_are_controllable(self, tmp_path):
        runner = FakeRunner()
        controller = make_controller(tmp_path, runner)
        reply = controller.handle({"op": "unit", "verb": "stop", "unit": "radon-cor.timer"}, peer_uid=os.getuid())
        assert reply["ok"] is True
        assert runner.calls[0][-3:] == ["unit", "stop", "radon-cor.timer"]

    def test_operator_failure_is_reported_with_its_returncode(self, tmp_path):
        runner = FakeRunner(rc=1, out="Job for radon-relay.service failed")
        controller = make_controller(tmp_path, runner)
        reply = controller.handle({"op": "unit", "verb": "start", "unit": "radon-relay.service"}, peer_uid=os.getuid())
        assert reply["ok"] is False
        assert reply["returncode"] == 1
        assert "failed" in reply["detail"]

    def test_real_runner_never_uses_a_shell(self, monkeypatch):
        seen = {}

        def fake_run(argv, **kw):
            seen["argv"] = argv
            seen["kw"] = kw
            return subprocess.CompletedProcess(argv, 0, "done", "")

        monkeypatch.setattr(serve.subprocess, "run", fake_run)
        rc, detail = serve.run_command(["/usr/bin/sudo", "-n", "/usr/local/bin/radon", "restart"], 5.0)
        assert rc == 0 and detail == "done"
        assert seen["kw"].get("shell", False) is False
        assert isinstance(seen["argv"], list)
        assert set(seen["kw"]["env"]) <= {"PATH", "LANG", "LC_ALL"}

    def test_real_runner_reports_timeout(self, monkeypatch):
        def fake_run(argv, **kw):
            raise subprocess.TimeoutExpired(argv, kw["timeout"])

        monkeypatch.setattr(serve.subprocess, "run", fake_run)
        rc, detail = serve.run_command(["/usr/bin/sudo", "-n", "/usr/local/bin/radon", "restart"], 5.0)
        assert rc == 124
        assert "timed out" in detail


class TestSelfProtection:
    @pytest.mark.parametrize("unit", ["radon-api.service", "radon-nextjs.service"])
    def test_stopping_a_panel_host_is_refused(self, tmp_path, unit):
        runner = FakeRunner()
        controller = make_controller(tmp_path, runner)
        reply = controller.handle({"op": "unit", "verb": "stop", "unit": unit}, peer_uid=os.getuid())
        assert reply["ok"] is False and reply["refused"] is True
        assert runner.calls == []

    @pytest.mark.parametrize("unit", ["radon-api.service", "radon-nextjs.service"])
    def test_restarting_a_panel_host_answers_first_then_runs_detached(self, tmp_path, unit):
        runner = FakeRunner()
        controller = make_controller(tmp_path, runner)
        reply = controller.handle({"op": "unit", "verb": "restart", "unit": unit}, peer_uid=os.getuid())
        assert reply["ok"] is True
        assert reply["accepted"] is True
        # Nothing ran before the reply went out.
        assert runner.calls == []
        assert len(controller._spawned) == 1
        controller._spawned[0]()
        assert runner.calls == [["/usr/bin/sudo", "-n", "/usr/local/bin/radon", "unit", "restart", unit]]

    def test_stack_restart_answers_first_then_runs_detached(self, tmp_path):
        runner = FakeRunner()
        controller = make_controller(tmp_path, runner)
        reply = controller.handle({"op": "stack-restart", "actor": "user_abc"}, peer_uid=os.getuid())
        assert reply["ok"] is True and reply["accepted"] is True
        assert runner.calls == []
        controller._spawned[0]()
        assert runner.calls == [["/usr/bin/sudo", "-n", "/usr/local/bin/radon", "restart"]]

    def test_detached_action_refuses_while_deploy_lock_is_held(self, tmp_path):
        import fcntl

        lock = tmp_path / "deploy.lock"
        lock.write_text("")
        handle = open(lock, "a+")
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            runner = FakeRunner()
            controller = make_controller(tmp_path, runner)
            reply = controller.handle({"op": "stack-restart"}, peer_uid=os.getuid())
        finally:
            handle.close()
        assert reply["ok"] is False
        assert reply["returncode"] == serve.LOCK_HELD_RC
        assert controller._spawned == []

    def test_second_detached_action_is_refused_while_one_is_pending(self, tmp_path):
        controller = make_controller(tmp_path, FakeRunner())
        first = controller.handle({"op": "stack-restart"}, peer_uid=os.getuid())
        second = controller.handle({"op": "unit", "verb": "restart", "unit": "radon-api.service"}, peer_uid=os.getuid())
        assert first["accepted"] is True
        assert second["ok"] is False
        assert second["returncode"] == serve.LOCK_HELD_RC

    def test_allowed_actions_per_unit(self):
        assert serve.allowed_actions("radon-relay.service") == ["start", "stop", "restart"]
        assert serve.allowed_actions("radon-api.service") == ["restart"]
        assert serve.allowed_actions("radon-nextjs.service") == ["restart"]
        assert serve.allowed_actions("radon-control.service") == []
        assert serve.allowed_actions("radon-ib-gateway.service") == []


class TestPeerAndAudit:
    def test_foreign_uid_is_refused(self, tmp_path):
        runner = FakeRunner()
        controller = make_controller(tmp_path, runner)
        reply = controller.handle({"op": "unit", "verb": "restart", "unit": "radon-relay.service"}, peer_uid=os.getuid() + 4242)
        assert reply["ok"] is False
        assert reply["error"] == "peer not allowed"
        assert runner.calls == []

    def test_unknown_peer_is_refused(self, tmp_path):
        controller = make_controller(tmp_path, FakeRunner())
        reply = controller.handle({"op": "ping"}, peer_uid=None)
        assert reply["ok"] is False

    def test_every_mutation_is_audited(self, tmp_path, capsys):
        controller = make_controller(tmp_path, FakeRunner())
        controller.handle({"op": "unit", "verb": "restart", "unit": "radon-relay.service", "actor": "user_abc"}, peer_uid=os.getuid())
        controller.handle({"op": "unit", "verb": "enable", "unit": "radon-relay.service", "actor": "user_abc"}, peer_uid=os.getuid())
        lines = [line for line in capsys.readouterr().err.splitlines() if "radon-control audit" in line]
        events = [json.loads(line.split("audit ", 1)[1]) for line in lines]
        assert events[0]["actor"] == "user_abc"
        assert events[0]["unit"] == "radon-relay.service"
        assert events[0]["verb"] == "restart"
        assert events[0]["result"] == "ok"
        assert events[1]["result"] == "refused"

    def test_actor_is_sanitized(self):
        assert serve.sanitize_actor("user_2abcDEF") == "user_2abcDEF"
        assert serve.sanitize_actor("evil\nINJECTED line") == "invalid"
        assert serve.sanitize_actor(None) == "unknown"
        assert serve.sanitize_actor("x" * 500) == "invalid"


class TestStatus:
    def test_status_returns_rows_with_allowed_actions(self, tmp_path):
        controller = make_controller(tmp_path, FakeRunner())
        reply = controller.handle({"op": "status"}, peer_uid=os.getuid())
        assert reply["ok"] is True
        rows = {row["Id"]: row for row in reply["units"]}
        assert rows["radon-api.service"]["allowed_actions"] == ["restart"]
        assert rows["radon-relay.service"]["allowed_actions"] == ["start", "stop", "restart"]
        assert rows["radon-control.service"]["allowed_actions"] == []

    def test_parse_show_blocks(self):
        raw = (
            "Id=radon-api.service\nDescription=Radon FastAPI server\nActiveState=active\n"
            "SubState=running\nActiveEnterTimestamp=Tue 2026-05-19 18:41:51 UTC\n"
            "\n"
            "Id=radon-cor.timer\nDescription=COR1M timer\nLastTriggerUSec=Wed 2026-05-20 10:00:00 UTC\n"
        )
        rows = serve.parse_show_blocks(raw)
        assert [r["Id"] for r in rows] == ["radon-api.service", "radon-cor.timer"]
        assert rows[0]["Description"] == "Radon FastAPI server"
        assert rows[1]["LastTriggerUSec"] == "Wed 2026-05-20 10:00:00 UTC"


def _short_socket_dir() -> str:
    # AF_UNIX paths are capped near 104 bytes on macOS; tmp_path is too long.
    return tempfile.mkdtemp(prefix="rc-", dir="/tmp")


class TestSocketEndToEnd:
    def _start(self, controller, monkeypatch, uid_override=None):
        directory = _short_socket_dir()
        path = os.path.join(directory, "control.sock")
        monkeypatch.setattr(serve, "peer_uid", lambda _sock: os.getuid() if uid_override is None else uid_override)
        server = serve.make_server(path, controller)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return server, path, directory

    def test_socket_is_owner_only_and_round_trips(self, tmp_path, monkeypatch):
        from api import host_control

        runner = FakeRunner(out="restart completed")
        controller = make_controller(tmp_path, runner)
        server, path, directory = self._start(controller, monkeypatch)
        try:
            assert oct(os.stat(path).st_mode & 0o777) == "0o600"
            monkeypatch.setenv("RADON_CONTROL_SOCKET", path)
            assert host_control.call({"op": "ping"}, timeout=2.0)["ok"] is True
            reply = host_control.call(
                {"op": "unit", "verb": "restart", "unit": "radon-relay.service", "actor": "user_abc"},
                timeout=2.0,
            )
            assert reply["ok"] is True
            assert runner.calls[-1][-3:] == ["unit", "restart", "radon-relay.service"]
        finally:
            server.shutdown()
            server.server_close()
            shutil.rmtree(directory, ignore_errors=True)

    def test_foreign_peer_gets_refused_over_the_socket(self, tmp_path, monkeypatch):
        from api import host_control

        runner = FakeRunner()
        controller = make_controller(tmp_path, runner)
        server, path, directory = self._start(controller, monkeypatch, uid_override=os.getuid() + 1)
        try:
            monkeypatch.setenv("RADON_CONTROL_SOCKET", path)
            reply = host_control.call({"op": "unit", "verb": "restart", "unit": "radon-relay.service"}, timeout=2.0)
            assert reply["ok"] is False
            assert runner.calls == []
        finally:
            server.shutdown()
            server.server_close()
            shutil.rmtree(directory, ignore_errors=True)

    def test_garbage_line_gets_an_error_reply_not_a_crash(self, tmp_path, monkeypatch):
        controller = make_controller(tmp_path, FakeRunner())
        server, path, directory = self._start(controller, monkeypatch)
        try:
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            client.settimeout(2.0)
            client.connect(path)
            client.sendall(b"rm -rf /\n")
            data = client.recv(4096)
            client.close()
            assert json.loads(data.decode())["ok"] is False
        finally:
            server.shutdown()
            server.server_close()
            shutil.rmtree(directory, ignore_errors=True)

    def test_bind_replaces_a_stale_socket_but_never_a_regular_file(self, monkeypatch):
        directory = _short_socket_dir()
        try:
            path = os.path.join(directory, "control.sock")
            Path(path).write_text("not a socket")
            with pytest.raises(OSError):
                serve.make_server(path, serve.Controller())
        finally:
            shutil.rmtree(directory, ignore_errors=True)


def test_unit_file_contract():
    unit = (_REPO_ROOT / "cloud" / "services" / "radon-control.service").read_text()
    assert "User=radon" in unit
    assert "RuntimeDirectory=radon-control" in unit
    assert "RuntimeDirectoryMode=0700" in unit
    # Kept across restarts so the api container's bind of the directory stays valid.
    assert "RuntimeDirectoryPreserve=yes" in unit
    assert "python -m scripts.control_service.serve" in unit
    # sudo needs setuid: NoNewPrivileges would silently break every action.
    assert "NoNewPrivileges=yes" not in unit
    # No TCP listener: the unit must not configure a port.
    assert "PORT" not in unit
    # Zero shared fate with the stack it controls.
    for dep in ("Requires=radon-", "BindsTo=radon-", "PartOf=radon-"):
        assert dep not in unit


def test_registry_parity_with_the_api_allowlist():
    """Every unit the daemon controls passes the API's own allowlist."""
    from api import services

    for unit in LOADED:
        if serve.allowed_actions(unit):
            assert services.is_valid_unit(unit)
    assert serve.CONTROL_UNIT not in services._PLACEHOLDER_UNITS
