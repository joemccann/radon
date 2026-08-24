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


# Live 2026-08-21 topology: deploy wiped data/flex_token_embargo.json.
# cash-flow-sync last_error is class=permanent (pre-lockout classifier)
# with code 1025 in the message and next_attempt_at Monday 08:00 ET.
LIVE_LAST_ATTEMPT = "2026-08-21T13:58:28.298780Z"
LIVE_MONDAY_WINDOW = "2026-08-24T12:00:00+00:00"
LIVE_1025_MESSAGE = (
    "ERR: Flex SendRequest failed (code 1025): "
    "Too many failed attempts. Please review your configuration."
)
MONDAY_0800_UTC = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
SUNDAY_2245_ET = datetime(2026, 8, 24, 2, 45, tzinfo=timezone.utc)


def _live_last_error(*, code: str, cls: str) -> dict:
    if code == "1025":
        message = LIVE_1025_MESSAGE
    else:
        message = (
            f"ERR: Flex SendRequest failed (code {code}): "
            "Token has expired."
        )
    return {
        "message": message,
        "class": cls,
        "next_attempt_at": LIVE_MONDAY_WINDOW,
    }


def _health_tuple(error: dict) -> tuple:
    return ("error", LIVE_LAST_ATTEMPT, json.dumps(error))


def _stub_turso_health(
    monkeypatch: pytest.MonkeyPatch, rows_by_service: dict[str, tuple]
) -> None:
    def fake(sql, args=(), timeout=None):  # noqa: ANN001
        key = args[0] if args else None
        if key in rows_by_service:
            return [rows_by_service[key]]
        if not args:
            return [row for row in rows_by_service.values() if row]
        return []

    import db.hrana_http as hrana_mod

    monkeypatch.setattr(hrana_mod, "hrana_execute", fake)
    monkeypatch.setattr(hrana_mod, "hrana_query", fake)


def _freeze_embargo_now(monkeypatch: pytest.MonkeyPatch, moment: datetime) -> None:
    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: ANN001
            if tz is None:
                return moment.replace(tzinfo=None)
            return moment.astimezone(tz)

    monkeypatch.setattr("utils.flex_embargo.datetime", _Frozen)


def _assert_reconstructed_deadline(raw: str | None) -> datetime:
    assert raw is not None
    parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    assert parsed.date().isoformat() == "2026-08-28"
    assert (parsed.hour, parsed.minute, parsed.second) == (13, 58, 28)
    assert parsed != MONDAY_0800_UTC
    return parsed


def test_missing_sidecar_reconstructs_live_1025_permanent_from_turso(
    sidecar: Path, monkeypatch: pytest.MonkeyPatch
):
    """No sidecar. Production cash-flow-sync row is class=permanent 1025.

    Deadline is last_attempt + 7d, not the stored Monday 08:00 window.
    """
    assert sidecar.exists() is False
    _stub_turso_health(
        monkeypatch,
        {"cash-flow-sync": _health_tuple(_live_last_error(code="1025", cls="permanent"))},
    )

    assert is_blocked(now=SUNDAY_2245_ET) is True
    assert is_blocked(now=MONDAY_0800_UTC) is True
    with pytest.raises(FlexTokenLocked, match="2026-08-28T13:58:28"):
        raise_if_blocked(now=MONDAY_0800_UTC)

    until = active_until(now=MONDAY_0800_UTC)
    _assert_reconstructed_deadline(until)

    payload = json.loads(sidecar.read_text())
    _assert_reconstructed_deadline(payload["next_attempt_at"])

    later = datetime(2026, 8, 28, 13, 58, 29, tzinfo=timezone.utc)
    assert is_blocked(now=later) is False


def test_permanent_1012_is_not_a_seven_day_lockout(
    sidecar: Path, monkeypatch: pytest.MonkeyPatch
):
    _stub_turso_health(
        monkeypatch,
        {"cash-flow-sync": _health_tuple(_live_last_error(code="1012", cls="permanent"))},
    )
    assert is_blocked(now=MONDAY_0800_UTC) is False
    raise_if_blocked(now=MONDAY_0800_UTC)
    assert sidecar.exists() is False


def test_fetch_statement_xml_skips_http_when_turso_1025_sidecar_missing(
    sidecar: Path, monkeypatch: pytest.MonkeyPatch
):
    import cash_flow_sync as cfs

    _stub_turso_health(
        monkeypatch,
        {"cash-flow-sync": _health_tuple(_live_last_error(code="1025", cls="permanent"))},
    )
    _freeze_embargo_now(monkeypatch, MONDAY_0800_UTC)
    calls: list[object] = []

    def _boom(*_a, **_k):  # noqa: ANN001
        calls.append(1)
        raise AssertionError("SendRequest during 1025 lockout")

    monkeypatch.setattr(cfs, "urlopen", _boom)
    with pytest.raises(FlexTokenLocked):
        cfs.fetch_statement_xml("tok", "1442520")
    assert calls == []
