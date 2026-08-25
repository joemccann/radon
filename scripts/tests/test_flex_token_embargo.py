"""Shared Flex-token lockout. 1025 is per token, not per consumer."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

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


@pytest.fixture
def sidecar_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """SIDECAR redirected, `_heartbeat` left REAL (T-100)."""
    path = tmp_path / "flex_token_embargo.json"
    monkeypatch.setattr("utils.flex_embargo.SIDECAR", path)
    return path


@pytest.fixture
def unwritable_sidecar(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """SIDECAR inside a 0o500 directory: every write_text raises OSError.
    Runner is non-root (uid 501), so the mode bit is honoured."""
    locked_dir = tmp_path / "ro"
    locked_dir.mkdir()
    locked_dir.chmod(0o500)
    path = locked_dir / "flex_token_embargo.json"
    monkeypatch.setattr("utils.flex_embargo.SIDECAR", path)
    yield path
    locked_dir.chmod(0o700)


class _ServiceHealthTable:
    """In-memory stand-in for the Turso `service_health` row.

    Writes go through a spy on ``db.writer.record_service_health`` and
    persist ``last_error`` exactly as the real upsert does (JSON text);
    reads come back through ``db.hrana_http.hrana_query`` in the same
    (state, last_attempt_finished_at, last_error) column order the
    rehydrate SELECT asks for.
    No live service is touched (feedback_test_pollution_to_production).
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.rows: dict[str, dict[str, Any]] = {}

    def record(self, service: str, state: str, *, started_at=None,
               finished_at=None, error: Optional[dict[str, Any]] = None) -> None:
        self.calls.append((service, state, dict(error or {})))
        self.rows[service] = {
            "service": service,
            "state": state,
            "last_attempt_started_at": started_at,
            "last_attempt_finished_at": finished_at,
            "last_error": json.dumps(error) if error else None,
            "updated_at": "2026-08-21T13:58:26Z",
        }

    def query(self, _sql: str, params: tuple = ()) -> list[tuple]:
        row = self.rows.get(params[0] if params else "")
        if not row:
            return []
        return [(
            row["state"],
            row["last_attempt_finished_at"],
            row["last_error"],
        )]

    def seed_lockout(self, next_attempt_at: str, code: str = "1025") -> None:
        self.record(
            "flex-web-service",
            "error",
            error={
                "message": f"Flex lockout (code {code}). Do not retry.",
                "class": "lockout",
                "code": code,
                "next_attempt_at": next_attempt_at,
            },
        )


@pytest.fixture
def service_health(monkeypatch: pytest.MonkeyPatch) -> _ServiceHealthTable:
    table = _ServiceHealthTable()
    monkeypatch.setattr("db.writer.record_service_health", table.record)
    monkeypatch.setattr("db.hrana_http.hrana_query", table.query, raising=False)
    return table


FRIDAY = datetime(2026, 8, 21, 13, 58, 26, tzinfo=timezone.utc)


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


def test_no_sidecar_and_no_service_health_row_is_not_blocked(
    sidecar_path: Path, service_health: _ServiceHealthTable
):
    assert service_health.rows == {}
    assert is_blocked(now=datetime.now(timezone.utc)) is False
    raise_if_blocked()
    assert sidecar_path.exists() is False


def test_service_health_row_blocks_without_a_sidecar(
    sidecar_path: Path, service_health: _ServiceHealthTable
):
    """T-100 (3): the durable row is the token-wide record. A lockout armed
    on Hetzner must block the laptop, whose data/ tree has no sidecar."""
    until = deadline_for(now=FRIDAY)
    service_health.seed_lockout(until)
    assert sidecar_path.exists() is False

    with pytest.raises(FlexTokenLocked, match="2026-08-28"):
        raise_if_blocked(now=FRIDAY + timedelta(days=1))
    assert is_blocked(now=FRIDAY + timedelta(days=1)) is True
    assert json.loads(sidecar_path.read_text())["next_attempt_at"] == until


def test_unwritable_sidecar_still_arms_the_token_wide_lockout(
    unwritable_sidecar: Path, service_health: _ServiceHealthTable
):
    """T-100 (1): a full / read-only data/ must not silently disarm the
    embargo. The service_health row carries it."""
    until = record_lockout("1025", now=FRIDAY)
    assert unwritable_sidecar.exists() is False
    assert until == deadline_for(now=FRIDAY)
    assert is_blocked(now=FRIDAY + timedelta(days=1)) is True


def test_record_lockout_dual_writes_service_health_and_reads_it_back(
    sidecar_path: Path, service_health: _ServiceHealthTable
):
    """T-100 (2): `_heartbeat` is NOT stubbed. Exactly one
    ("flex-web-service", "error") row carrying next_attempt_at, and that
    row alone (sidecar removed) reproduces the deadline."""
    until = record_lockout("1025", now=FRIDAY)

    assert [(c[0], c[1]) for c in service_health.calls] == [("flex-web-service", "error")]
    error = service_health.calls[0][2]
    assert error["next_attempt_at"] == until
    assert error["code"] == "1025"
    assert error["class"] == "lockout"

    sidecar_path.unlink()
    assert active_until(now=FRIDAY + timedelta(days=1)) == until


def test_record_lockout_raises_when_neither_sink_lands(
    unwritable_sidecar: Path, monkeypatch: pytest.MonkeyPatch
):
    """Nothing recorded must not read as recorded."""
    from db.hrana_http import HranaHttpError
    from utils.flex_embargo import FlexLockoutNotRecorded

    def _outage(*_a: Any, **_k: Any) -> None:
        raise HranaHttpError("TimeoutError: timed out")

    monkeypatch.setattr("db.writer.record_service_health", _outage)
    with pytest.raises(FlexLockoutNotRecorded, match="2026-08-28"):
        record_lockout("1025", now=FRIDAY)


def test_lapsed_service_health_row_neither_blocks_nor_rehydrates(
    sidecar_path: Path, service_health: _ServiceHealthTable
):
    service_health.seed_lockout(deadline_for(now=FRIDAY))
    later = FRIDAY + timedelta(days=LOCKOUT_DAYS)
    assert is_blocked(now=later) is False
    assert sidecar_path.exists() is False


def test_service_health_outage_fails_open_without_raising(
    sidecar_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Read side: a Turso outage must never turn raise_if_blocked() into a
    crash. Documented fail-open (the sidecar is still consulted first)."""
    from db.hrana_http import HranaHttpError

    def _outage(*_a: Any, **_k: Any) -> None:
        raise HranaHttpError("TimeoutError: timed out")

    monkeypatch.setattr("db.hrana_http.hrana_query", _outage, raising=False)
    assert is_blocked(now=FRIDAY) is False
    raise_if_blocked(now=FRIDAY)


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
