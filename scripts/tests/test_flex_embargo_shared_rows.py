"""§1#22: a LOCAL sidecar lapse must not clear the SHARED lockout record.

The laptop and Hetzner share IB_FLEX_TOKEN and one Turso `service_health`
table but keep separate sidecars. When this host's sidecar lapsed,
`active_until` wrote `ok` to the shared rows unconditionally:

  * another host's later, still-live lockout in `flex-web-service` was
    erased, so the next caller here fired SendRequest into a live IBKR 1025
    and extended it;
  * `cash-flow-sync`'s row, which this module does not own, was overwritten;
  * the `ok` was read-then-write with no CAS, so a lockout recorded between
    the read and the write was erased too;
  * the clear marker was a plain write_text (a torn read drops suppression).
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from utils import flex_embargo as fe  # noqa: E402

T0 = datetime(2026, 8, 21, 13, 58, 26, tzinfo=timezone.utc)


class _Table:
    """`service_health` keyed by service: (state, last_attempt_finished_at, last_error)."""

    def __init__(self) -> None:
        self.rows: dict[str, tuple] = {}
        self.writes: list[tuple[str, str]] = []
        self.before_update: Optional[Callable[[], None]] = None

    def record(self, service: str, state: str, *, started_at=None, finished_at=None,
               error: Optional[dict[str, Any]] = None) -> None:
        self.writes.append((service, state))
        prior = self.rows.get(service)
        finished = finished_at or (prior[1] if prior else None)
        self.rows[service] = (state, finished, json.dumps(error) if error else None)

    def query(self, _sql: str, params: tuple = (), timeout=None) -> list[tuple]:
        row = self.rows.get(params[0] if params else "")
        return [row] if row else []

    def execute(self, sql: str, params: tuple = (), timeout=None) -> None:
        assert "UPDATE service_health" in sql, sql
        if self.before_update is not None:
            self.before_update()
        service, observed = params[-2], params[-1]
        self.writes.append((service, "cas"))
        row = self.rows.get(service)
        if row and row[0] == "error" and row[2] == observed:
            self.rows[service] = ("ok", row[1], None)

    def seed_lockout(self, until: str) -> None:
        self.record(fe.SERVICE, "error", error={
            "message": "Flex lockout (code 1025). Do not retry.",
            "class": "lockout", "code": "1025", "next_attempt_at": until,
        })


@pytest.fixture
def table(tmp_path, monkeypatch) -> _Table:
    t = _Table()
    monkeypatch.setattr(fe, "SIDECAR", tmp_path / "flex_token_embargo.json")
    monkeypatch.setattr(fe, "_durable_store_available", lambda: True)
    monkeypatch.setattr("db.writer.record_service_health", t.record)
    monkeypatch.setattr("db.hrana_http.hrana_query", t.query, raising=False)
    monkeypatch.setattr("db.hrana_http.hrana_execute", t.execute, raising=False)
    return t


def _state(table: _Table, service: str) -> Optional[str]:
    row = table.rows.get(service)
    return row[0] if row else None


class TestALocalLapseLeavesTheSharedRecordAlone:
    def test_another_hosts_live_lockout_survives_this_hosts_lapse(self, table):
        fe.record_lockout("1025", now=T0)
        # The other host hit a fresh 1025 six days later (its own sidecar was
        # clear), so the shared row now carries a later deadline.
        later_until = fe.deadline_for(now=T0 + timedelta(days=6))
        table.seed_lockout(later_until)

        local_lapse = T0 + timedelta(days=fe.LOCKOUT_DAYS, minutes=1)
        assert fe.active_until(now=local_lapse) == later_until, (
            "the local sidecar lapse erased another host's live lockout"
        )
        assert _state(table, fe.SERVICE) == "error"
        assert fe.is_blocked(now=local_lapse + timedelta(days=1)) is True

    def test_the_shared_row_is_cleared_once_its_own_deadline_lapsed(self, table):
        fe.record_lockout("1025", now=T0)
        lapse = T0 + timedelta(days=fe.LOCKOUT_DAYS, minutes=1)
        assert fe.active_until(now=lapse) is None
        assert _state(table, fe.SERVICE) == "ok"

    def test_a_lapse_never_writes_the_cash_flow_sync_row(self, table):
        table.record(fe.CASH_FLOW_SERVICE, "error", finished_at=fe._utc_iso(T0),
                     error={"message": "cash_flow_sync --from-file failed (exit 3)"})
        table.writes.clear()
        fe.record_lockout("1025", now=T0)
        assert fe.active_until(now=T0 + timedelta(days=8)) is None
        assert [w for w in table.writes if w[0] == fe.CASH_FLOW_SERVICE] == [], (
            "flex_embargo overwrote a row owned by the Flex ingest"
        )
        assert _state(table, fe.CASH_FLOW_SERVICE) == "error"

    def test_the_lapse_ok_is_compare_and_set(self, table):
        """A lockout recorded between the read and the write must survive."""
        fe.record_lockout("1025", now=T0)
        lapse = T0 + timedelta(days=fe.LOCKOUT_DAYS, minutes=1)
        fresh_until = fe.deadline_for(now=lapse)
        table.before_update = lambda: table.seed_lockout(fresh_until)
        fe.active_until(now=lapse)
        assert _state(table, fe.SERVICE) == "error"
        assert json.loads(table.rows[fe.SERVICE][2])["next_attempt_at"] == fresh_until


class TestClearOwnsOnlyItsRow:
    def test_clear_does_not_write_the_cash_flow_sync_row(self, table):
        fe.record_lockout("1025", now=T0)
        table.writes.clear()
        fe.clear(now=T0 + timedelta(hours=1))
        assert [w for w in table.writes if w[0] == fe.CASH_FLOW_SERVICE] == []
        assert _state(table, fe.SERVICE) == "ok"

    def test_clear_outlasts_the_marker_window_against_older_cash_flow_evidence(self, table):
        """Without the cash-flow reset, pre-clear 1025 evidence there must not re-arm."""
        table.record(fe.CASH_FLOW_SERVICE, "error", finished_at=fe._utc_iso(T0), error={
            "message": "Too many failed attempts", "code": "1025",
        })
        cleared_at = T0 + timedelta(hours=1)
        fe.clear(now=cleared_at)
        past_marker = cleared_at + timedelta(hours=fe.CLEAR_SUPPRESSION_HOURS + 1)
        assert fe.is_blocked(now=past_marker) is False

    def test_a_new_cash_flow_lockout_after_the_clear_still_blocks(self, table):
        cleared_at = T0
        fe.clear(now=cleared_at)
        table.record(fe.CASH_FLOW_SERVICE, "error",
                     finished_at=fe._utc_iso(T0 + timedelta(days=2)),
                     error={"message": "Too many failed attempts", "code": "1025"})
        assert fe.is_blocked(now=T0 + timedelta(days=3)) is True

    def test_the_clear_marker_is_written_atomically(self, table, monkeypatch):
        replaced: list[tuple[str, str]] = []
        real_replace = fe.os.replace

        def spy(src, dst):
            replaced.append((str(src), str(dst)))
            return real_replace(src, dst)

        monkeypatch.setattr(fe.os, "replace", spy)
        fe.clear(now=T0)
        assert any(dst == str(fe._cleared_path()) for _, dst in replaced), (
            "the clear marker is a plain write_text; a torn read drops suppression"
        )
        assert fe._cleared_marker_covers(T0 + timedelta(hours=1)) is True
