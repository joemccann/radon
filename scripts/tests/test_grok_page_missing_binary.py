"""Page 3b990bb8496e24785a14a4df32e79a4e (2026-09-30 00:05Z): the
oneshot paged P1 Result=exit-code because GROK_BIN was absent.

subprocess.run raises FileNotFoundError before any return code.
resolve_latest only catches GrokRuntimeError, so the refuse path
never ran and run_cycle exited 1 every timer fire. A missing binary
is a failed probe: no last-known-good refuses the cycle (exit 0,
paused heartbeat, page left pending, no fallback pushover). An LKG
record falls back instead of raising.
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
    warnings: list[str] = []
    claims: list[str] = []
    monkeypatch.setattr(
        responder,
        "record_cycle_health",
        lambda state, **_k: heartbeats.append(state),
    )
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
        refuse, leave the page pending, and not pushover a fallback."""
        rc = responder.run_cycle(cycle_env["root"], now=NOW)

        assert rc == 0
        assert cycle_env["claims"] == []
        assert cycle_env["heartbeats"] == ["paused"]
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
