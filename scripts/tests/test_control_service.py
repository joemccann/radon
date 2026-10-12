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


class FakeLogRunner:
    """Stdout-capturing runner stand-in for the read-only `logs` op."""

    def __init__(self, rc: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.calls: list[tuple[list[str], float]] = []
        self.rc = rc
        self.stdout = stdout
        self.stderr = stderr

    def __call__(self, argv, timeout):
        self.calls.append((list(argv), timeout))
        return self.rc, self.stdout, self.stderr


def _jline(us: int, message, priority="6") -> str:
    row = {"__REALTIME_TIMESTAMP": str(us), "__CURSOR": "s=abc", "MESSAGE": message}
    if priority is not None:
        row["PRIORITY"] = priority
    return json.dumps(row)


class TestLogs:
    def _controller(self, tmp_path, log_runner):
        return make_controller(tmp_path, FakeRunner(), log_runner=log_runner)

    def test_logs_execs_the_sudoers_granted_operator_argv(self, tmp_path):
        runner = FakeLogRunner(stdout=_jline(1_760_000_000_123_456, "boom", "3") + "\n")
        controller = self._controller(tmp_path, runner)
        reply = controller.handle(
            {"op": "logs", "unit": "radon-cor.service", "actor": "user_abc"}, peer_uid=os.getuid(),
        )
        assert reply["ok"] is True, reply
        assert runner.calls == [(
            ["/usr/bin/sudo", "-n", "/usr/local/bin/radon", "logs", "radon-cor.service"],
            serve.LOGS_TIMEOUT_S,
        )]
        assert serve.LOGS_TIMEOUT_S == 15.0
        assert reply["unit"] == "radon-cor.service"
        assert reply["truncated"] is False
        assert reply["entries"] == [
            {"ts": "2025-10-09T08:53:20.123456Z", "priority": 3, "message": "boom"},
        ]

    @pytest.mark.parametrize("unit", [
        "radon-cor.timer",  # journal is per service; the API maps timer -> service
        "radon-api",
        "radon-beta-api.service",
        "radon-unknown.service",  # well formed, not loaded
        "radon-ib-gateway-preheld-restart.service",  # internal: fails is_valid_unit
        "ssh.service",
        "-n 9999",
        "--since=yesterday",
        "radon-api.service; id",
        "",
        7,
        None,
    ])
    def test_logs_refuses_without_exec(self, tmp_path, unit):
        runner = FakeLogRunner()
        controller = self._controller(tmp_path, runner)
        request = {"op": "logs", "actor": "x"}
        if unit is not None:
            request["unit"] = unit
        reply = controller.handle(request, peer_uid=os.getuid())
        assert reply["ok"] is False and reply["refused"] is True
        assert runner.calls == []

    def test_logs_refuses_extra_fields(self, tmp_path):
        runner = FakeLogRunner()
        controller = self._controller(tmp_path, runner)
        reply = controller.handle(
            {"op": "logs", "unit": "radon-cor.service", "lines": 5000}, peer_uid=os.getuid(),
        )
        assert reply["ok"] is False and reply["refused"] is True
        assert runner.calls == []

    def test_logs_refuses_a_foreign_peer(self, tmp_path):
        runner = FakeLogRunner()
        controller = self._controller(tmp_path, runner)
        reply = controller.handle({"op": "logs", "unit": "radon-cor.service"}, peer_uid=os.getuid() + 4242)
        assert reply["ok"] is False
        assert runner.calls == []

    def test_logs_success_is_not_audited_but_failure_is(self, tmp_path, capsys):
        ok = self._controller(tmp_path, FakeLogRunner(stdout=""))
        ok.handle({"op": "logs", "unit": "radon-cor.service", "actor": "a"}, peer_uid=os.getuid())
        assert "radon-control audit" not in capsys.readouterr().err
        bad = self._controller(tmp_path, FakeLogRunner(rc=1, stderr="Failed to open journal"))
        reply = bad.handle({"op": "logs", "unit": "radon-cor.service", "actor": "a"}, peer_uid=os.getuid())
        assert reply["ok"] is False and not reply.get("refused")
        assert "Failed to open journal" in reply["error"]
        assert reply["returncode"] == 1
        lines = [l for l in capsys.readouterr().err.splitlines() if "radon-control audit" in l]
        event = json.loads(lines[0].split("audit ", 1)[1])
        assert event["op"] == "logs" and event["unit"] == "radon-cor.service"
        assert event["result"] == "failed"

    def test_logs_reports_a_timeout(self, tmp_path):
        controller = self._controller(tmp_path, FakeLogRunner(rc=serve.TIMEOUT_RC, stderr="timed out after 15s"))
        reply = controller.handle({"op": "logs", "unit": "radon-cor.service"}, peer_uid=os.getuid())
        assert reply["ok"] is False
        assert reply["returncode"] == serve.TIMEOUT_RC

    def test_unloaded_registry_failure_is_an_error_not_a_crash(self, tmp_path):
        def boom():
            raise OSError("dbus down")

        runner = FakeLogRunner()
        controller = serve.Controller(
            runner=FakeRunner(), log_runner=runner, loaded_units=boom,
            unit_rows=lambda: [], deploy_lock_path=tmp_path / "l", spawn=lambda fn: None,
        )
        reply = controller.handle({"op": "logs", "unit": "radon-cor.service"}, peer_uid=os.getuid())
        assert reply["ok"] is False
        assert runner.calls == []


class TestLogsReplyBudget:
    def test_non_ascii_journal_still_fits_the_socket_reply_cap(self, tmp_path):
        # 300 x 2000 non-ASCII chars json-escape to ~3.6 MB; host_control
        # refuses any reply over 2 MiB, which would hide the whole journal.
        raw = "\n".join(_jline(1_000_000 + i, "\u00e9" * 2500, "3") for i in range(300))
        controller = make_controller(tmp_path, FakeRunner(), log_runner=FakeLogRunner(stdout=raw))
        reply = controller.handle({"op": "logs", "unit": "radon-cor.service"}, peer_uid=os.getuid())
        assert reply["ok"] is True
        encoded = (json.dumps(reply, separators=(",", ":")) + "\n").encode("utf-8")
        assert len(encoded) <= serve.host_control.MAX_REPLY_BYTES
        assert reply["truncated"] is True
        assert 0 < len(reply["entries"]) < 300
        # The newest lines are the ones kept.
        assert reply["entries"][-1]["ts"] == serve.parse_journal_json(raw)[0][-1]["ts"]

    def test_fit_reply_budget_keeps_everything_that_fits(self):
        entries = [{"ts": "t", "priority": 6, "message": "m"}] * 3
        kept, dropped = serve.journal_entries.fit_reply_budget(entries, 10_000)
        assert kept == entries and dropped is False


class TestJournalParsing:
    def test_orders_oldest_to_newest_and_parses_priority(self):
        raw = "\n".join([
            _jline(1_000_000, "first", "6"),
            _jline(2_000_000, "second", "4"),
            _jline(3_000_000, "third", None),
        ])
        entries, truncated = serve.parse_journal_json(raw, limit=300)
        assert [e["message"] for e in entries] == ["first", "second", "third"]
        assert [e["priority"] for e in entries] == [6, 4, None]
        assert entries[0]["ts"] == "1970-01-01T00:00:01.000000Z"
        assert truncated is False

    def test_byte_array_message_is_decoded_with_replacement(self):
        raw = _jline(1_000_000, list("héllo".encode("utf-8")) + [0xFF], "3")
        entries, _ = serve.parse_journal_json(raw, limit=300)
        assert entries[0]["message"] == "héllo�"

    def test_bad_json_and_non_object_lines_are_skipped(self):
        raw = "\n".join([
            "not json at all",
            "[1, 2]",
            _jline(1_000_000, "kept"),
            '{"MESSAGE": "no timestamp"}',
            '{"__REALTIME_TIMESTAMP": "abc", "MESSAGE": "bad ts"}',
            "",
        ])
        entries, _ = serve.parse_journal_json(raw, limit=300)
        assert [e["message"] for e in entries] == ["kept"]

    def test_null_message_is_reported_not_dropped(self):
        # journalctl -o json prints null for a field over 4096 bytes.
        entries, _ = serve.parse_journal_json(_jline(1_000_000, None), limit=300)
        assert len(entries) == 1
        assert "too large" in entries[0]["message"]

    def test_long_message_is_capped(self):
        entries, _ = serve.parse_journal_json(_jline(1_000_000, "x" * 5000), limit=300)
        assert len(entries[0]["message"]) <= serve.MAX_LOG_MESSAGE_CHARS == 2000

    def test_truncated_when_the_tail_limit_is_reached(self):
        raw = "\n".join(_jline(i * 1_000_000, f"m{i}") for i in range(1, 6))
        entries, truncated = serve.parse_journal_json(raw, limit=5)
        assert len(entries) == 5 and truncated is True
        _, short = serve.parse_journal_json(raw, limit=300)
        assert short is False

    def test_non_numeric_priority_is_null(self):
        entries, _ = serve.parse_journal_json(_jline(1_000_000, "m", "warn"), limit=300)
        assert entries[0]["priority"] is None


# Fake credential-shaped values, built at runtime so the secret scanner never
# sees a keyword next to a literal value in this source.
_FAKE_HEX = "".join(["0f1e2d3c", "4b5a6978", "8796a5b4", "c3d2e1f0"])
_FAKE_A = "".join(["abcDEF", "12345678"])
_FAKE_B = "".join(["abcDEF", "123456"])
_FAKE_C = "".join(["abcdEFGH", "1234"])


class TestRedaction:
    @pytest.mark.parametrize("text,secret", [
        ("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1In0.c2lnbmF0dXJl", "eyJhbGciOiJIUzI1NiJ9"),
        ("GET /api?token=abc123def456&x=1", "abc123def456"),
        ("UW_TOKEN=" + _FAKE_HEX, _FAKE_HEX[:16]),
        ("api_key=SuperSecretValue99", "SuperSecretValue99"),
        ("client_secret=hunter2hunter2", "hunter2hunter2"),
        ("password=correct-horse-battery", "correct-horse-battery"),
        ('{"password": "p@ssw0rd!"}', "p@ssw0rd!"),
        ("using key sk-ant-api03-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789", "AbCdEfGhIjKlMnOp"),
        ("digest " + "a1" * 32, "a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1"),
        ("blob Zm9vYmFyYmF6cXV4UXV1eFF1dXhRdXV4UXV1eDEyMzQ1Ng end", "Zm9vYmFyYmF6cXV4UXV1eFF1"),
        # Colon forms: HTTP headers, YAML-ish config dumps, Python dict reprs.
        ("password: hunter2hunter2", "hunter2hunter2"),
        ("x-api-key: " + _FAKE_A, _FAKE_A),
        ("{'api_key': '" + _FAKE_B + "'}", _FAKE_B),
        ('auth_token: "' + _FAKE_C + '"', _FAKE_C),
        ("Authorization: Basic dXNlcjpwYXNzd29yZA==", "dXNlcjpwYXNzd29yZA=="),
        ("dsn postgres://admin:S3cretPw@db.host:5432/x", "S3cretPw"),
        ("jwt eyJhbGciOiJFZERTQSJ9.eyJpYXQiOjE3MH0.QWxhZGRpbg", "eyJpYXQiOjE3MH0"),
    ])
    def test_secrets_are_redacted(self, text, secret):
        out = serve.redact_secrets(text)
        assert secret not in out
        assert "[REDACTED]" in out

    @pytest.mark.parametrize("text", [
        "KeyError: 'radon-api.service'",
        "Traceback (most recent call last):",
        "deployed 607f4adf0123456789abcdef0123456789abcdef",  # 40-hex git SHA stays
        "page 77becdbdfc506a32c447c10283efe6b4 acked",  # 32-hex page id stays
        "/home/radon/radon/scripts/api/server.py line 42",
        "radon-ib-gateway-preheld-restart.service failed with result exit-code",
        "GET https://radon.run/api/admin/logs 200",
        "sort_key: ticker",
        "at 12:30:01 key rotation done",
    ])
    def test_diagnostics_survive(self, text):
        assert serve.redact_secrets(text) == text

    def test_parsed_entries_are_redacted(self):
        entries, _ = serve.parse_journal_json(_jline(1_000_000, "auth Bearer abcdefghijklmnop"), limit=300)
        assert "abcdefghijklmnop" not in entries[0]["message"]


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
