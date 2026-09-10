"""REL-205 (R-568): the Robinhood rotation double-fault disables cleanly.

If the fallback write ALSO fails, the raw PermissionError escaped before
`_disable_for_process`: the rotated token existed nowhere, the flag stayed
False, and the process re-spent the spent refresh token next call.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from clients import robinhood_client as rc  # noqa: E402


class TestDoubleFaultDisables:
    def test_fallback_failure_still_disables_and_raises_typed(self, monkeypatch, tmp_path):
        store = rc.RobinhoodTokenStore.__new__(rc.RobinhoodTokenStore)
        store._state = {"access_token": "new-tok", "refresh_token": "r2"}
        store._path = tmp_path / "tokens.json"

        monkeypatch.setattr(rc, "_refresh_disabled", False)
        disabled = []
        monkeypatch.setattr(
            rc, "_disable_for_process", lambda reason: disabled.append(reason)
        )

        def broken_fallback():
            raise PermissionError(13, "Permission denied")

        monkeypatch.setattr(store, "_save_rotation_fallback", broken_fallback)

        exc = OSError(13, "Permission denied")
        with pytest.raises(rc.RobinhoodClientError):
            store._handle_commit_failure(exc)
        assert disabled, (
            "the double-fault escaped without _disable_for_process — the "
            "spent token would be re-spent by this process"
        )
        assert "NOWHERE" in disabled[0] or "nowhere" in disabled[0].lower()

    def test_single_fault_path_unchanged(self, monkeypatch, tmp_path):
        store = rc.RobinhoodTokenStore.__new__(rc.RobinhoodTokenStore)
        store._state = {"access_token": "new-tok"}
        store._path = tmp_path / "tokens.json"
        monkeypatch.setattr(rc, "_refresh_disabled", False)
        disabled = []
        monkeypatch.setattr(
            rc, "_disable_for_process", lambda reason: disabled.append(reason)
        )
        saved_to = tmp_path / "fallback.json"
        monkeypatch.setattr(store, "_save_rotation_fallback", lambda: saved_to)
        with pytest.raises(rc.RobinhoodClientError, match="restore them"):
            store._handle_commit_failure(OSError(28, "No space left"))
        assert disabled and str(saved_to) in disabled[0]
