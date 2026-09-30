"""Page 3b990bb8496e24785a14a4df32e79a4e (2026-09-30 00:05Z): the
oneshot paged P1 Result=exit-code because GROK_BIN was absent.

subprocess.run raises FileNotFoundError before any return code.
resolve_latest only catches GrokRuntimeError, so the refuse path
never ran and run_cycle exited 1 every timer fire. A missing binary
is a failed probe: no last-known-good refuses the cycle (exit 0,
`error` heartbeat so the watchdog still alerts, page left pending, no
fallback pushover). An LKG record falls back instead of raising, but only
to a binary that is safe to exec: never a temp dir, a pytest basetemp,
the clone, a missing path or a world-writable file.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import grok_page_responder as responder  # noqa: E402
import grok_runtime  # noqa: E402
from watchdog import pages as pages_mod  # noqa: E402

NOW = datetime(2026, 9, 30, 0, 5, tzinfo=timezone.utc)
_MISSING = "/home/radon/.local/bin/grok"


def _page() -> dict:
    return {
        "page_id": "3b990bb8496e24785a14a4df32e79a4e",
        "service": "radon-grok-page-responder.service",
        "severity": "P1",
        "kind": "unit",
        "message_excerpt": (
            "<untrusted-excerpt>systemd unit failed "
            "(Result=exit-code, NRestarts=0)</untrusted-excerpt>"
        ),
        "paged_at": "2026-09-30T00:05:00.209243Z",
        "status": "pending",
        "claimed_at": None,
        "attempts": 0,
    }


@pytest.fixture
def cycle_env(monkeypatch, tmp_path):
    monkeypatch.setenv("GROK_PAGE_RESPONDER", "1")
    monkeypatch.setenv("GROK_PAGE_AUTOSHIP", "0")
    monkeypatch.setenv("GROK_PAGE_AUTOPUSH", "0")
    monkeypatch.setenv("GROK_PAGE_SYNC_REMOTE", "0")
    monkeypatch.setenv("GROK_BIN", str(tmp_path / "no-such-grok"))
    monkeypatch.setenv("RADON_GROK_LKG_PATH", str(tmp_path / "no-lkg.json"))
    heartbeats: list[str] = []
    errors: list[dict | None] = []
    warnings: list[str] = []
    claims: list[str] = []

    def _record(state, **kwargs):
        heartbeats.append(state)
        errors.append(kwargs.get("error"))

    monkeypatch.setattr(responder, "record_cycle_health", _record)
    monkeypatch.setattr(
        responder, "_send_pin_warning", lambda message: warnings.append(message)
    )
    monkeypatch.setattr(pages_mod, "actions_since", lambda **_k: 0)
    monkeypatch.setattr(pages_mod, "list_actionable_pages", lambda **_k: [_page()])
    monkeypatch.setattr(
        pages_mod,
        "claim_page",
        lambda page_id, **_k: claims.append(page_id) or True,
    )
    return {
        "root": tmp_path,
        "heartbeats": heartbeats,
        "errors": errors,
        "warnings": warnings,
        "claims": claims,
        "lock": tmp_path / "data" / "cache" / "grok_pages" / responder.LOCK_NAME,
    }


class TestMissingGrokBinary:
    def test_missing_bin_without_lkg_does_not_fail_the_oneshot(
        self, cycle_env, capsys
    ):
        """00:05Z: probe of an absent GROK_BIN raised FileNotFoundError
        out of run_cycle. systemd recorded exit-code. The cycle must
        refuse, leave the page pending, and not pushover a fallback. It
        must still surface: `paused` is fresh every 30s and never alerts,
        so the row is `error` with the reason for the watchdog."""
        rc = responder.run_cycle(cycle_env["root"], now=NOW)

        assert rc == 0
        assert cycle_env["claims"] == []
        assert cycle_env["heartbeats"] == ["error"]
        assert "no last-known-good" in cycle_env["errors"][0]["message"]
        assert cycle_env["warnings"] == []
        assert not cycle_env["lock"].exists()
        out = capsys.readouterr().out
        assert '"skipped": "grok_runtime"' in out
        assert "no last-known-good" in out

    def test_missing_bin_with_lkg_falls_back_instead_of_raising(self, tmp_path):
        """The same FileNotFoundError on --version is a probe failure.
        An LKG record is the runtime, not an uncaught spawn error."""
        lkg_path = tmp_path / "grok_lkg.json"
        lkg_path.write_text(
            '{"cli_version":"1.0.3","binary_path":"'
            + str(tmp_path / "lkg-grok")
            + '","model":"grok-4.6","reasoning_effort":"high",'
            '"promoted_at":"2026-09-01T00:00:00Z",'
            '"smoke_result":"stand_down: previous"}',
            encoding="utf-8",
        )
        lkg = grok_runtime.load_lkg(lkg_path)

        def runner(argv, **_kwargs):
            raise FileNotFoundError(2, "No such file or directory", _MISSING)

        resolved = grok_runtime.resolve_latest(
            grok_bin=_MISSING, runner=runner, lkg=lkg
        )

        assert resolved.refused is False
        assert resolved.used_fallback is True
        assert resolved.source == "lkg"
        assert resolved.model == "grok-4.6"
        assert resolved.binary_path == str(tmp_path / "lkg-grok")

    def test_permission_error_on_a_present_binary_still_fails_the_probe(self):
        """A binary that exists but cannot be exec'd is not the missing-path
        class. It must still escape so the oneshot stays exit-code."""

        def runner(argv, **_kwargs):
            raise PermissionError(13, "Permission denied", argv[0])

        with pytest.raises(PermissionError):
            grok_runtime.resolve_latest(grok_bin=_MISSING, runner=runner)


def _write_lkg(path: Path, binary: Path) -> Path:
    path.write_text(
        '{"cli_version":"1.0.3","binary_path":"'
        + str(binary)
        + '","model":"grok-4.6","reasoning_effort":"high",'
        '"promoted_at":"2026-09-01T00:00:00Z",'
        '"smoke_result":"stand_down: previous"}',
        encoding="utf-8",
    )
    return path


def _exe(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)
    return path


@pytest.fixture
def trusted_tmp(monkeypatch):
    """tmp_path lives under the system temp dir and a pytest basetemp,
    which the validator refuses by design. Lift both so a single case can
    stand in for /var/lib/radon/grok-upgrade."""
    monkeypatch.setattr(grok_runtime, "_untrusted_roots", lambda: [])
    monkeypatch.setattr(grok_runtime, "UNTRUSTED_PATH_MARKERS", ())


class TestLkgBinaryIsTrusted:
    def test_tmp_and_pytest_basetemp_binaries_are_refused(self, tmp_path):
        """The 2026-09-29 relink pointed the CLI into a pytest basetemp."""
        exe = _exe(tmp_path / "scratch" / "candidate" / "bin" / "grok")
        problem = grok_runtime.lkg_binary_problem(str(exe))
        assert problem is not None
        assert "pytest" in problem or "untrusted" in problem

    def test_tmp_dir_is_refused_without_pytest_marker(self, tmp_path, monkeypatch):
        monkeypatch.setattr(grok_runtime, "UNTRUSTED_PATH_MARKERS", ())
        exe = _exe(tmp_path / "grok")
        assert "untrusted" in grok_runtime.lkg_binary_problem(str(exe))

    @pytest.mark.parametrize(
        "binary, expected",
        [("", "absolute"), ("grok", "absolute"), ("/nonexistent/grok", "missing")],
    )
    def test_relative_empty_and_missing_are_refused(self, binary, expected):
        assert expected in grok_runtime.lkg_binary_problem(binary)

    def test_non_executable_and_world_writable_are_refused(
        self, tmp_path, trusted_tmp
    ):
        plain = tmp_path / "plain"
        plain.write_text("x", encoding="utf-8")
        plain.chmod(0o644)
        assert grok_runtime.lkg_binary_problem(str(plain)) == "not executable"
        loose = _exe(tmp_path / "loose")
        loose.chmod(0o777)
        assert grok_runtime.lkg_binary_problem(str(loose)) == "world-writable"
        folder = tmp_path / "dir"
        folder.mkdir()
        assert grok_runtime.lkg_binary_problem(str(folder)) == "not a regular file"

    def test_symlink_into_untrusted_dir_is_refused(self, tmp_path, monkeypatch):
        """A trusted-looking link whose target is in a temp root is refused."""
        monkeypatch.setattr(grok_runtime, "UNTRUSTED_PATH_MARKERS", ())
        evil_root = tmp_path / "evil"
        target = _exe(evil_root / "grok")
        link_dir = tmp_path / "safe"
        link_dir.mkdir()
        (link_dir / "grok").symlink_to(target)
        monkeypatch.setattr(grok_runtime, "_untrusted_roots", lambda: [evil_root])
        assert "untrusted" in grok_runtime.lkg_binary_problem(str(link_dir / "grok"))

    def test_extra_untrusted_root_refuses_the_clone(self, tmp_path, trusted_tmp):
        exe = _exe(tmp_path / "clone" / "bin" / "grok")
        assert grok_runtime.lkg_binary_problem(str(exe)) is None
        assert "untrusted" in grok_runtime.lkg_binary_problem(
            str(exe), extra_untrusted=(tmp_path / "clone",)
        )

    def test_untrusted_lkg_refuses_the_cycle_instead_of_exec(
        self, cycle_env, monkeypatch
    ):
        """GROK_BIN missing plus an LKG pointing into /tmp: no fallback exec,
        same error row as no LKG at all."""
        exe = _exe(cycle_env["root"] / "tmp-grok")
        lkg = _write_lkg(cycle_env["root"] / "grok_lkg.json", exe)
        monkeypatch.setenv("RADON_GROK_LKG_PATH", str(lkg))

        rc = responder.run_cycle(cycle_env["root"], now=NOW)

        assert rc == 0
        assert cycle_env["claims"] == []
        assert cycle_env["heartbeats"] == ["error"]
        assert cycle_env["warnings"] == []

    def test_trusted_lkg_is_launched_when_grok_bin_is_missing(
        self, cycle_env, monkeypatch, trusted_tmp, tmp_path_factory
    ):
        # Outside the clone (cycle_env root), standing in for the upgrader's
        # /var/lib/radon/grok-upgrade scratch.
        exe = _exe(tmp_path_factory.mktemp("upgrade") / "candidate" / "grok")
        lkg = _write_lkg(cycle_env["root"] / "grok_lkg.json", exe)
        monkeypatch.setenv("RADON_GROK_LKG_PATH", str(lkg))
        monkeypatch.setenv(
            "RADON_GROK_RUNTIME_LOCK", str(cycle_env["root"] / "runtime.lock")
        )
        launched: list[list[str]] = []

        def probe(argv, **_k):
            raise FileNotFoundError(2, "No such file or directory", argv[0])

        monkeypatch.setattr(responder, "_default_grok_runner", probe)
        monkeypatch.setattr(pages_mod, "record_attempt_failure", lambda *a, **k: "pending")
        monkeypatch.setattr(
            responder, "attempt_oneshot_rerun", lambda *a, **k: None
        )

        def runner(cmd, **_k):
            launched.append(list(cmd))
            return type("P", (), {"returncode": 1, "stdout": "", "stderr": "x"})()

        monkeypatch.setattr(responder, "_runtime_from_track", _wrap_probe(probe))
        rc = responder.run_cycle(cycle_env["root"], now=NOW, grok_runner=runner)

        assert rc == 0
        assert launched and launched[0][0] == str(exe)
        assert cycle_env["warnings"], "fallback still pushovers the LKG warning"


def _wrap_probe(probe):
    original = responder._runtime_from_track

    def wrapped(**kwargs):
        # run_cycle skips the probe when a grok_runner is injected; force it
        # so the missing GROK_BIN path is the one exercised.
        kwargs["probe"] = True
        kwargs["grok_runner"] = probe
        return original(**kwargs)

    return wrapped
