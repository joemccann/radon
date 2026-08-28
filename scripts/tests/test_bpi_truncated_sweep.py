"""A truncated BPI sweep must not persist as a complete reading.

R-224: the `SWEEP_BUDGET_S` cut-off drops un-fetched members and returns
whatever partial `fetched` it has, but the only completeness guard is
`latest["members"] < MIN_LATEST_COVERAGE * member_count` — and `aggregate_bpi`
CARRY-FORWARDS each member's last known state across sessions. A member frozen
at yesterday's P&F state still counts toward `resolved`, so the 80% gate can
never detect a truncated sweep. Concretely: RUT's 1996-member walk breaks at
chunk 20/100, 400 members carry today's bar so `all_dates` includes the last
completed session, `stale` is computed False, and the payload is persisted and
mirrored as a fresh, complete BPI that is 80% yesterday's breadth. The only
trace is a stderr line and `sources.member_close_fetches`, which nothing reads.

R-225: `run_scan` builds ONE deadline covering only the Yahoo fetch phase.
Every index still runs its full unbudgeted Turso phase afterwards —
`_read_stored_max_dates`, `_store_fetched`, ~100 chunked `_read_stored_closes`
SELECTs for RUT alone, plus `upsert_bpi_history_rows`. The slack between
`SWEEP_BUDGET_S` and `TimeoutStartSec` must absorb all of that for three
indices, so a run whose FETCH stopped exactly on budget can still be SIGTERMed
inside the write, leaving `bpi_history` partially written and the
`service_cycle` `finally` unexecuted — no error row at all. That is the
`Result=timeout, NRestarts=0` failure 26168ed5 set out to eliminate, moved from
the fetch phase to the persist phase.
"""

from __future__ import annotations

import contextlib
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import bpi_scan as bpi  # noqa: E402
from utils import market_calendar  # noqa: E402

_ET = ZoneInfo("America/New_York")

# T-227: `last_completed_session_date` flips at exactly 16:00 ET, and this
# module used to resolve it TWICE per test — once to build the session
# fixtures, once inside `build_index_payload` to compute `stale`. A run that
# straddled the close built against yesterday and graded against today, so
# `assert payload["stale"] is False` reds for reasons that have nothing to do
# with the code under test. One frozen trading day, injected everywhere.
_ANCHOR_SESSION = "2026-08-26"  # a Wednesday, no holiday


@pytest.fixture(autouse=True)
def _pin_session_anchor(monkeypatch):
    """Freeze the session anchor for the whole module (see _ANCHOR_SESSION)."""
    monkeypatch.setattr(
        bpi, "last_completed_session_date", lambda *_a, **_k: _ANCHOR_SESSION
    )


def _sessions(n: int, anchor: str = _ANCHOR_SESSION) -> list[str]:
    out: list[str] = []
    day = date.fromisoformat(anchor)
    while len(out) < n:
        if day.weekday() < 5:
            out.append(day.isoformat())
        day -= timedelta(days=1)
    return sorted(out)


def _series(dates: list[str], state: str = "buy") -> tuple[list[str], list[str]]:
    return (dates, [state] * len(dates))


def _payload(member_series, member_count):
    return bpi.build_index_payload(
        index_symbol="RUT",
        member_series=member_series,
        member_count=member_count,
        taken_at="2099-01-01T00:00:00Z",
        sources={"constituents": "seed", "member_close_fetches": {"yahoo": 0, "stored": 0}},
    )


class TestTruncatedSweepIsDetected:
    def test_carry_forward_members_do_not_satisfy_the_coverage_gate(self):
        """1000 members: 200 fetched today, 800 frozen at yesterday's bar."""
        sessions = _sessions(60)
        today, yesterday = sessions[-1], sessions[-2]

        member_series = {}
        for i in range(200):
            member_series[f"FRESH{i}"] = _series(sessions)
        for i in range(800):
            member_series[f"FROZEN{i}"] = _series(sessions[:-1])

        payload = _payload(member_series, member_count=1000)
        assert payload.get("missing") is True, (
            "a sweep that fetched 20% of members was published as complete: "
            f"members={payload.get('members')} stale={payload.get('stale')}"
        )
        assert payload.get("reason") == "insufficient_coverage"
        assert today != yesterday

    def test_a_complete_sweep_still_publishes(self):
        sessions = _sessions(60)
        member_series = {f"M{i}": _series(sessions) for i in range(1000)}
        payload = _payload(member_series, member_count=1000)
        assert payload.get("missing") is False
        assert payload["members"] == 1000
        assert payload["stale"] is False

    def test_a_sweep_just_over_the_threshold_publishes(self):
        sessions = _sessions(60)
        member_series = {f"M{i}": _series(sessions) for i in range(850)}
        member_series.update({f"OLD{i}": _series(sessions[:-1]) for i in range(150)})
        payload = _payload(member_series, member_count=1000)
        assert payload.get("missing") is False

    def test_the_payload_states_how_many_members_reported_today(self):
        sessions = _sessions(60)
        member_series = {f"M{i}": _series(sessions) for i in range(900)}
        member_series.update({f"OLD{i}": _series(sessions[:-1]) for i in range(100)})
        payload = _payload(member_series, member_count=1000)
        assert payload["members_fresh"] == 900, (
            "nothing in the payload distinguished a fetched member from a "
            "carried-forward one"
        )
        assert payload["members"] == 1000


class TestPersistPhaseHasBudget:
    def test_the_fetch_budget_reserves_room_for_the_write_phase(self):
        unit = (
            Path(__file__).resolve().parents[2]
            / "cloud" / "services" / "radon-bpi.service"
        ).read_text(encoding="utf-8")
        timeout = int(re.search(r"TimeoutStartSec=(\d+)", unit).group(1))
        assert bpi.SWEEP_BUDGET_S + bpi.PERSIST_RESERVE_S <= timeout, (
            f"fetch budget {bpi.SWEEP_BUDGET_S}s + persist reserve "
            f"{bpi.PERSIST_RESERVE_S}s exceeds TimeoutStartSec={timeout}s"
        )
        assert bpi.PERSIST_RESERVE_S >= 300, (
            "the write phase is ~130 chunked Hrana SELECTs plus the upserts "
            "for three indices; the reserve must be a real budget"
        )

    def test_a_sigterm_during_the_write_still_writes_an_error_row(self, monkeypatch):
        """SIGTERM's default disposition terminates without unwinding, so the
        service_cycle `finally` never ran and there was no error row at all."""
        import signal

        installed: dict = {}

        def fake_signal(sig, handler):
            installed[sig] = handler

        monkeypatch.setattr(signal, "signal", fake_signal)
        bpi.install_sigterm_unwind()
        assert signal.SIGTERM in installed, (
            "no SIGTERM handler, so a systemd kill skips the cycle's finally"
        )
        with pytest.raises(SystemExit):
            installed[signal.SIGTERM](signal.SIGTERM, None)

    def test_run_scan_itself_installs_the_handler(self, monkeypatch, tmp_path):
        """T-231(a): the test above calls `install_sigterm_unwind()` directly,
        so deleting `run_scan`'s call to it left the suite green while a
        systemd `Result=timeout` again killed the process without unwinding
        `service_cycle`'s finally. Pin the CALL SITE: spy `signal.signal` and
        assert the handler lands as part of run_scan's own execution."""
        import signal

        installed: dict = {}
        monkeypatch.setattr(
            signal, "signal", lambda sig, handler: installed.__setitem__(sig, handler)
        )
        monkeypatch.setenv(bpi.DATA_DIR_ENV, str(tmp_path))

        bpi.run_scan([], backfill=False, no_db=True)

        assert signal.SIGTERM in installed, (
            "run_scan did not install the SIGTERM unwind during its own run; a "
            "systemd timeout skips service_cycle's finally and writes no error row"
        )
        with pytest.raises(SystemExit):
            installed[signal.SIGTERM](signal.SIGTERM, None)


class _FakeClock:
    """Only `monotonic` is used by the code paths under test."""

    def __init__(self, now: float):
        self.now = now

    def monotonic(self) -> float:
        return self.now


class TestPersistReserveGatesTheWrite:
    """T-231(b): `PERSIST_RESERVE_S` was read by nothing but the arithmetic
    assertion above — a "reserve" that production ignored. run_scan now gates
    each index's Turso write on `time.monotonic() < deadline + PERSIST_RESERVE_S`.
    """

    def _run(self, monkeypatch, tmp_path, *, now, sweep_deadline, reserve):
        monkeypatch.setenv(bpi.DATA_DIR_ENV, str(tmp_path))
        monkeypatch.setattr(bpi, "time", _FakeClock(now))
        monkeypatch.setattr(bpi, "PERSIST_RESERVE_S", reserve)

        persisted: list[str] = []
        monkeypatch.setattr(
            bpi, "_persist_index", lambda symbol, _payload: persisted.append(symbol)
        )
        monkeypatch.setattr(
            bpi,
            "scan_index",
            lambda symbol, **_kw: {
                "index_symbol": symbol,
                "missing": False,
                "taken_at": "2099-01-01T00:00:00Z",
                "_bpi_rows": [],
            },
        )
        monkeypatch.setattr(
            bpi,
            "_heartbeat_cycle",
            contextlib.contextmanager(lambda _no_db: iter([None])),
        )

        bpi.run_scan(
            ["RUT"], backfill=False, no_db=False, sweep_deadline=sweep_deadline
        )
        return persisted

    def test_a_write_inside_the_reserve_still_persists(self, monkeypatch, tmp_path):
        persisted = self._run(
            monkeypatch, tmp_path, now=1_000.0, sweep_deadline=900.0, reserve=600
        )
        assert persisted == ["RUT"], (
            "100s past the fetch budget with a 600s reserve is the normal case; "
            "the gate must not change real-world behaviour"
        )

    def test_a_write_past_the_reserve_is_skipped(self, monkeypatch, tmp_path):
        persisted = self._run(
            monkeypatch, tmp_path, now=1_000.0, sweep_deadline=300.0, reserve=600
        )
        assert persisted == [], (
            "700s past the fetch budget exhausted the 600s reserve, yet the "
            "Turso write was started anyway — that is the SIGTERM-mid-upsert "
            "window R-225 set out to close"
        )

    def test_the_reserve_value_is_what_moves_the_gate(self, monkeypatch, tmp_path):
        """Mutating PERSIST_RESERVE_S must change observable behaviour: same
        clock, same deadline, only the constant differs."""
        args = dict(now=1_000.0, sweep_deadline=300.0)
        assert self._run(monkeypatch, tmp_path, reserve=600, **args) == []
        assert self._run(monkeypatch, tmp_path, reserve=5_000, **args) == ["RUT"]


class TestTheSessionAnchorIsResolvedOnce:
    """T-227: the module's fixtures and `build_index_payload` must agree on ONE
    session, or a run that straddles 16:00 ET reds on `stale`."""

    def test_the_1600_et_flip_is_real(self):
        before = market_calendar.last_completed_session_date(
            datetime(2026, 8, 27, 15, 59, tzinfo=_ET)
        )
        after = market_calendar.last_completed_session_date(
            datetime(2026, 8, 27, 16, 0, tzinfo=_ET)
        )
        assert (before, after) == ("2026-08-26", "2026-08-27"), (
            "the anchor flips at exactly 16:00 ET on a trading day"
        )

    def test_a_straddling_run_would_have_reddened_the_old_two_call_anchoring(
        self, monkeypatch
    ):
        """Deliberately re-resolve per call, exactly as this module used to:
        the build consumes 15:59 ET, the `stale` computation consumes 16:00 ET.
        """
        flip = iter(["2026-08-26", "2026-08-27"])
        monkeypatch.setattr(
            bpi,
            "last_completed_session_date",
            lambda *_a, **_k: next(flip, "2026-08-27"),
        )
        sessions = _sessions(60, anchor=bpi.last_completed_session_date())
        payload = _payload(
            {f"M{i}": _series(sessions) for i in range(1000)}, member_count=1000
        )
        assert payload["stale"] is True, (
            "the straddle no longer diverges, so this module's false-red is "
            "not what T-227 described"
        )

    def test_the_pinned_anchor_does_not_flip_across_calls(self):
        assert bpi.last_completed_session_date() == _ANCHOR_SESSION
        assert bpi.last_completed_session_date() == _ANCHOR_SESSION
        assert _sessions(60)[-1] == _ANCHOR_SESSION

    def test_a_complete_sweep_is_never_stale_under_the_pinned_anchor(self):
        sessions = _sessions(60)
        payload = _payload(
            {f"M{i}": _series(sessions) for i in range(1000)}, member_count=1000
        )
        assert payload["as_of_session"] == _ANCHOR_SESSION
        assert payload["stale"] is False
