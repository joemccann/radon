"""The login-throttle reader fails closed on unreadable watchdog state.

Audit 2026-11-03 §1 #21: a missing OR unreadable watchdog state file read as
"login allowed". Missing means no episode was ever recorded and stays open;
an unreadable or corrupt file may be hiding a live IBKR throttle, so it now
blocks logins for one base quiet period measured from the file's mtime.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from utils import ib_login_throttle as lt

MTIME = 1_800_000_000.0


def _write(path: Path, raw: str) -> Path:
    path.write_text(raw)
    os.utime(path, (MTIME, MTIME))
    return path


def test_missing_file_means_no_episode(tmp_path):
    assert lt.login_throttle_retry_at(tmp_path / "absent.json") is None


def test_no_episode_recorded(tmp_path):
    path = _write(tmp_path / "s.json", json.dumps({"login_throttle_since": 0}))
    assert lt.login_throttle_retry_at(path) is None


def test_recorded_episode(tmp_path):
    path = _write(tmp_path / "s.json", json.dumps({
        "login_throttle_since": 1000.0, "login_throttle_retries": 1,
    }))
    assert lt.login_throttle_retry_at(path) == 1000.0 + lt.login_throttle_cooldown(1)


@pytest.mark.parametrize("raw", [
    "{garbage",
    "",
    "[]",
    "null",
    json.dumps({"login_throttle_since": "yesterday"}),
    json.dumps({"login_throttle_since": 1000.0, "login_throttle_retries": "x"}),
])
def test_unreadable_state_fails_closed_for_one_quiet_period(tmp_path, raw):
    path = _write(tmp_path / "s.json", raw)
    assert lt.login_throttle_retry_at(path) == MTIME + lt.LOGIN_THROTTLE_COOLDOWN_BASE_SECS


def test_unreadable_file_permissions_fail_closed(tmp_path, monkeypatch):
    path = _write(tmp_path / "s.json", "{}")

    def denied(self, *a, **k):
        raise PermissionError("denied")

    monkeypatch.setattr(Path, "read_text", denied)
    assert lt.login_throttle_retry_at(path) == MTIME + lt.LOGIN_THROTTLE_COOLDOWN_BASE_SECS
