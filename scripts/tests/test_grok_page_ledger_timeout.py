"""Page a1c550c1 (2026-09-27 00:15Z): a hrana read timeout on the
watchdog_pages ledger escaped run_cycle. systemd recorded
Result=exit-code, NRestarts=0, and the poller paged itself.

The same TimeoutError on the poller's own heartbeat is already
non-fatal. A ledger read timeout must exit 0, release the lock, and
skip the ok heartbeat so a standing Turso outage still goes stale.
A statement error is not a read timeout and must still fail the unit.
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
from db.hrana_http import HranaHttpError  # noqa: E402
from watchdog import pages as pages_mod  # noqa: E402

NOW = datetime(2026, 9, 27, 0, 15, tzinfo=timezone.utc)
_TIMEOUT = "TimeoutError: The read operation timed out"


def _page() -> dict:
    return {
        "page_id": "a1c550c1f43ba50af5bd701cb697acd4",
        "service": "radon-grok-page-responder.service",
        "severity": "P1",
        "kind": "unit",
        "message_excerpt": (
            "<untrusted-excerpt>systemd unit failed "
            "(Result=exit-code, NRestarts=0)</untrusted-excerpt>"
        ),
        "paged_at": "2026-09-27T00:15:04Z",
        "status": "pending",
        "claimed_at": None,
        "attempts": 0,
    }


class _Proc:
    returncode = 0
    stdout = "RESULT: stand_down | ok\n"
    stderr = ""


@pytest.fixture
def cycle_env(monkeypatch, tmp_path):
    monkeypatch.setenv("GROK_PAGE_RESPONDER", "1")
    monkeypatch.setenv("GROK_PAGE_AUTOSHIP", "0")
    monkeypatch.setenv("GROK_PAGE_AUTOPUSH", "0")
    monkeypatch.setenv("GROK_PAGE_SYNC_REMOTE", "0")
    heartbeats: list[str] = []
    followups: list[dict] = []
    invocations: list = []
    monkeypatch.setattr(
        responder,
        "record_cycle_health",
        lambda state, **_k: heartbeats.append(state),
    )
    monkeypatch.setattr(
        responder, "_send_followup", lambda **kwargs: followups.append(kwargs)
    )
    monkeypatch.setattr(pages_mod, "actions_since", lambda **_k: 0)
    monkeypatch.setattr(pages_mod, "list_actionable_pages", lambda **_k: [_page()])
    monkeypatch.setattr(pages_mod, "claim_page", lambda *_a, **_k: True)

    def runner(*_a, **_k):
        invocations.append(_a)
        return _Proc()

    return {
        "root": tmp_path,
        "heartbeats": heartbeats,
        "followups": followups,
        "invocations": invocations,
        "runner": runner,
        "lock": tmp_path / "data" / "cache" / "grok_pages" / responder.LOCK_NAME,
    }


def _timeout(*_a, **_k):
    raise HranaHttpError(_TIMEOUT)


class TestLedgerReadTimeout:
    def test_claim_timeout_does_not_fail_the_oneshot(
        self, cycle_env, monkeypatch, capsys
    ):
        """00:17:17Z: claim_page's hrana execute raised HranaHttpError
        TimeoutError. The oneshot must not exit 1, must not launch grok,
        and must not mark itself healthy."""
        monkeypatch.setattr(pages_mod, "claim_page", _timeout)

        rc = responder.run_cycle(
            cycle_env["root"], now=NOW, grok_runner=cycle_env["runner"]
        )

        assert rc == 0
        assert cycle_env["invocations"] == []
        assert cycle_env["heartbeats"] == []
        assert cycle_env["followups"] == []
        assert not cycle_env["lock"].exists()
        err = capsys.readouterr().err
        assert "grok page ledger non-fatal" in err
        assert _TIMEOUT in err

    def test_complete_timeout_does_not_fail_the_oneshot(
        self, cycle_env, monkeypatch, capsys
    ):
        """00:13:22Z: grok had already finished and complete_page raised
        the same read timeout. Exiting 1 paged the poller after the work.
        Exit 0, no ok heartbeat, no follow-up for a page that is not done."""
        monkeypatch.setattr(pages_mod, "complete_page", _timeout)

        rc = responder.run_cycle(
            cycle_env["root"], now=NOW, grok_runner=cycle_env["runner"]
        )

        assert rc == 0
        assert len(cycle_env["invocations"]) == 1
        assert cycle_env["heartbeats"] == []
        assert cycle_env["followups"] == []
        assert not cycle_env["lock"].exists()
        err = capsys.readouterr().err
        assert "grok page ledger non-fatal" in err
        assert _TIMEOUT in err

    def test_list_timeout_does_not_fail_the_oneshot(
        self, cycle_env, monkeypatch, capsys
    ):
        monkeypatch.setattr(pages_mod, "list_actionable_pages", _timeout)

        rc = responder.run_cycle(
            cycle_env["root"], now=NOW, grok_runner=cycle_env["runner"]
        )

        assert rc == 0
        assert cycle_env["invocations"] == []
        assert cycle_env["heartbeats"] == []
        assert not cycle_env["lock"].exists()
        assert "grok page ledger non-fatal" in capsys.readouterr().err

    def test_statement_error_still_fails_the_oneshot(self, cycle_env, monkeypatch):
        """A ledger statement error is the cycle failing, not a read stall.
        It must still escape so systemd records exit-code."""

        def boom(*_a, **_k):
            raise HranaHttpError("no such table: watchdog_pages")

        monkeypatch.setattr(pages_mod, "claim_page", boom)

        with pytest.raises(HranaHttpError, match="no such table"):
            responder.run_cycle(
                cycle_env["root"], now=NOW, grok_runner=cycle_env["runner"]
            )

        assert cycle_env["heartbeats"] == []
        assert cycle_env["invocations"] == []
        assert not cycle_env["lock"].exists()
