"""Missing responder Turso token must stand the cycle down, not crash it.

The stripped env no longer copies production TURSO_AUTH_TOKEN. When
TURSO_RESPONDER_AUTH_TOKEN is absent the dest file has no consumer token,
and hrana raises ``TURSO_DB_URL / TURSO_AUTH_TOKEN not configured``.
Claim/complete/heartbeat need writes, so the cycle skips those steps,
logs the key name (never a value), and exits 0.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import grok_page_responder as responder  # noqa: E402
from db.hrana_http import HranaHttpError  # noqa: E402
from watchdog import pages as pages_mod  # noqa: E402

NOW = datetime(2026, 10, 1, 14, 30, tzinfo=timezone.utc)
_MISSING = "TURSO_DB_URL / TURSO_AUTH_TOKEN not configured"


def _missing(*_a, **_k):
    raise HranaHttpError(_MISSING)


def test_missing_turso_token_skips_ledger_and_exits_zero(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("GROK_PAGE_RESPONDER", "1")
    monkeypatch.setenv("GROK_PAGE_AUTOSHIP", "0")
    monkeypatch.setenv("GROK_PAGE_AUTOPUSH", "0")
    monkeypatch.setenv("GROK_PAGE_SYNC_REMOTE", "0")
    monkeypatch.delenv("TURSO_AUTH_TOKEN", raising=False)
    heartbeats: list[str] = []
    invocations: list = []
    monkeypatch.setattr(
        responder, "record_cycle_health", lambda state, **_k: heartbeats.append(state)
    )
    monkeypatch.setattr(pages_mod, "list_actionable_pages", _missing)
    monkeypatch.setattr(pages_mod, "actions_since", _missing)
    monkeypatch.setattr(pages_mod, "claim_page", _missing)

    def never(*_a, **_k):
        invocations.append(_a)
        raise AssertionError("grok must not run without a Turso token")

    rc = responder.run_cycle(tmp_path, now=NOW, grok_runner=never)
    out = capsys.readouterr()
    printed = out.out + out.err

    assert rc == 0
    assert invocations == []
    assert heartbeats == []
    assert "TURSO_AUTH_TOKEN" in printed
    assert "skip Turso-dependent steps" in printed
    payload = json.loads(out.out.strip().splitlines()[-1])
    assert payload["skipped"] == "turso_token_missing"
    assert "eyJ" not in printed
    lock = tmp_path / "data" / "cache" / "grok_pages" / responder.LOCK_NAME
    assert not lock.exists()
