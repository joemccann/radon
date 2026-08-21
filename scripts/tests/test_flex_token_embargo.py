"""Shared Flex-token lockout. 1025 is per token, not per consumer."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from utils.flex_embargo import (
    FlexTokenLocked,
    LOCKOUT_DAYS,
    active_until,
    deadline_for,
    is_blocked,
    is_lockout_code,
    raise_if_blocked,
    record_lockout,
)


@pytest.fixture
def sidecar(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "flex_token_embargo.json"
    monkeypatch.setattr("utils.flex_embargo.SIDECAR", path)
    monkeypatch.setattr("utils.flex_embargo._heartbeat", lambda *a, **k: None)
    return path


def test_1025_is_the_lockout_code():
    assert is_lockout_code("1025") is True
    assert is_lockout_code("1001") is False
    assert is_lockout_code("1018") is False


def test_record_lockout_outlasts_the_next_monday_window(sidecar: Path):
    friday = datetime(2026, 8, 21, 13, 58, 26, tzinfo=timezone.utc)
    monday_0800 = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
    until = record_lockout("1025", now=friday)
    assert datetime.fromisoformat(until.replace("Z", "+00:00")) >= monday_0800 + timedelta(days=1)
    assert is_blocked(now=monday_0800) is True
    with pytest.raises(FlexTokenLocked, match="2026-08-28"):
        raise_if_blocked(now=monday_0800)


def test_a_lapsed_lockout_clears(sidecar: Path):
    friday = datetime(2026, 8, 21, 13, 58, 26, tzinfo=timezone.utc)
    record_lockout("1025", now=friday)
    later = friday + timedelta(days=LOCKOUT_DAYS)
    assert active_until(now=later) is None
    assert sidecar.exists() is False


def test_deadline_is_seven_days():
    now = datetime(2026, 8, 21, 13, 58, 26, tzinfo=timezone.utc)
    assert deadline_for(now=now) == "2026-08-28T13:58:26Z"


def test_no_sidecar_is_not_blocked(sidecar: Path):
    assert is_blocked(now=datetime.now(timezone.utc)) is False
    raise_if_blocked()


def test_sidecar_shape(sidecar: Path):
    record_lockout("1025", now=datetime(2026, 8, 21, tzinfo=timezone.utc))
    payload = json.loads(sidecar.read_text())
    assert payload["code"] == "1025"
    assert "next_attempt_at" in payload
