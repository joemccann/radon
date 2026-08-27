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

import re
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import bpi_scan as bpi  # noqa: E402


def _sessions(n: int) -> list[str]:
    out: list[str] = []
    day = bpi.last_completed_session_date()
    day = date.fromisoformat(day)
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
