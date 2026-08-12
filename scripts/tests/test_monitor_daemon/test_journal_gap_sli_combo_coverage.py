#!/usr/bin/env python3
"""Regression: journal-gap-sli / journal-reconcile false-positive an option
COMBO whose legs are fully journaled (production incident 2026-08-11 — both
``journal-gap-sli`` and ``journal-reconcile`` service_health rows state=error
reporting the SAME 10 exec ids).

Production reproduction (verified against Turso, not invented)
--------------------------------------------------------------
Running the live detector against production reproduced the incident payload
byte for byte::

    scan_journal_gaps(db, min_age_minutes=10)
    -> executed_orders_scanned=52  missing_exec_id_count=10

    GAP 000243ef.6a79db0c.03.01.01  ('MU C1000', '1000.0', 'C', '20260828')
    GAP 000243ef.6a79db0c.02.01.01  ('MU C875',   '875.0', 'C', '20260828')
    GAP 0000f665.6a79be15.03.01.01  ('MU C1000', '1000.0', 'C', '20260828')
    ... (10 total, all perm_id 1200238014)

    journal contract_dates for the same contracts:
      ('MU    260828C00875000',  '875.0', 'C', '20260828', '2026-08-10')
      ('MU    260828C01000000', '1000.0', 'C', '20260828', '2026-08-10')

The topology
------------
One MU 875/1000 call spread (perm_id 1200238014, orderId 269) filled in five
partial executions. IB emitted, per partial fill:

  * one BAG parent execution   -> exec_id ``<root>.01.01``,   symbol "MU Spread"
  * two per-LEG executions     -> exec_id ``<root>.02.01.01`` (875C, BOT)
                                          ``<root>.03.01.01`` (1000C, SLD)

15 rows in ``executed_orders``; the 5 BAG parents are skipped by design, the
10 leg fills are the phantom gaps.

The mechanism (two independent halves, both required)
-----------------------------------------------------
1. ``ib_orders.py:fetch_executed_orders`` writes
   ``payload["symbol"] = format_contract(contract)`` — a DISPLAY string
   ("MU C875" / "MU C1000" / "MU Spread"), NOT the underlying. The underlying
   survives only in ``payload["contract"]["symbol"]``.
   ``_extract_contract_key`` reads ``payload["symbol"]`` FIRST, so the
   executed side keys on "MU C875".

2. Those ten fills ARE journaled: the Flex importer (``journal_rehydrate``
   grouping, ``decision="IB_AUTO_IMPORT"``) collapsed them into two
   ``+``-joined composite rows whose ``ib_exec_id`` lives in the FLEX
   execution-id space ("10035166430+...") and whose ``ticker`` is
   ``exec_obj.symbol`` — the OCC local symbol "MU    260828C00875000".
   ``_build_journal_coverage`` keys on that verbatim.

So the exec-id exact match cannot fire (disjoint id spaces: IB ``.02.01.01``
form vs Flex integers) and ``_has_nearby_journal_row``'s per-contract ±1-day
fallback — which exists exactly to absorb these rehydrate-shaped rows — cannot
fire either, because the two sides disagree on the TICKER component while
strike / right / expiry / date all match:

    executed_orders key : ('MU C875', '875.0', 'C', '20260828')
    journal key         : ('MU    260828C00875000', '875.0', 'C', '20260828', <date>)

Result: 10 phantom gaps, both sensors latched state=error, on a journal that is
economically complete.

The blast radius is wider than the Flex importer
------------------------------------------------
The executed side keys on the DISPLAY string for EVERY option, so the ±1-day
fallback is dead for every option row in the journal, whichever writer produced
it — the two journal writers just disagree in different directions:

    journal_sync.py:989   (REAL-TIME)  ticker = contract.symbol   -> "MU"
    journal_rehydrate:438 (FLEX)       ticker = exec_obj.symbol   -> OCC local

Neither equals "MU C875". Real-time-journaled fills escape the incident only
because their exec ids match EXACTLY and ``_find_gaps`` short-circuits before
ever reaching the fallback. So a fix must normalise BOTH journal shapes and the
executed side onto one key — keying the executed side on
``contract["localSymbol"]`` would repair the Flex case while leaving every
real-time row permanently unreachable by the fallback.

Controls that keep a fix honest
-------------------------------
* A genuinely unjournaled combo leg must still be reported (no blanket suppress).
* A plain-underlying (real-time-written) journal row must become reachable via
  the ±1-day fallback when the exec id does NOT match — this is the control that
  rejects a localSymbol-shaped fix behaviourally rather than by white-box pin.
* The SAME combo topology journaled by the REAL-TIME path with exec ids that DO
  match exactly (production SLV 70/60 call spread) must stay at zero gaps — a
  ticker-normalisation fix must not regress exact-match coverage.
* A fill younger than ``GAP_SLI_MIN_AGE_MINUTES`` must stay unreported even when
  genuinely unjournaled — the fix must not reach for the age gate.

All dates are window-relative (today-minus-N); no live Turso; account id
anonymised.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))

from monitor_daemon.handlers.journal_gap_sli import (  # noqa: E402
    GAP_SLI_MIN_AGE_MINUTES,
    JournalGapSliHandler,
    scan_journal_gaps,
)
from monitor_daemon.handlers.journal_reconcile import (  # noqa: E402
    JournalReconcileHandler,
    _build_journal_coverage,
    _extract_contract_key,
)

# ---------------------------------------------------------------------------
# Production constants (MU 875/1000 call spread, perm_id 1200238014)
# ---------------------------------------------------------------------------

UNDERLYING = "MU"
PERM_ID = 1200238014
ORDER_ID = 269
CLIENT_ID = 47
ORDER_REF = "radon-4290641448fb4165a7f7"
ACCOUNT_ID = "U0000000"  # anonymised; production value is a real IBKR account

LONG_STRIKE = 875.0
SHORT_STRIKE = 1000.0
LONG_CON_ID = 899911985
SHORT_CON_ID = 899911766

# (bag_parent_exec_id, long_leg_exec_id, short_leg_exec_id, quantity) — the five
# partial executions of the single combo order, exactly as IB delivered them.
COMBO_PARTIAL_FILLS: list[tuple[str, str, str, float]] = [
    ("0001505f.6a79d2a7.01.01", "0000dcdf.6a79e272.02.01.01", "0000dcdf.6a79e272.03.01.01", 3.0),
    ("0001505f.6a79d2aa.01.01", "0000f665.6a79be15.02.01.01", "0000f665.6a79be15.03.01.01", 1.0),
    ("0001505f.6a79d2ac.01.01", "0001108f.6a79cbbd.02.01.01", "0001108f.6a79cbbd.03.01.01", 1.0),
    ("0001505f.6a79d2ae.01.01", "0001de5f.6a7a46f3.02.01.01", "0001de5f.6a7a46f3.03.01.01", 3.0),
    ("0001505f.6a79d2b0.01.01", "000243ef.6a79db0c.02.01.01", "000243ef.6a79db0c.03.01.01", 1.0),
]

# Flex execution ids for the same ten fills, '+'-joined by the Flex importer
# into one composite journal row per leg.
FLEX_EXEC_IDS_LONG_LEG = [
    "10035166430", "10035166436", "10035168728", "10035171550", "10035171824",
]
FLEX_EXEC_IDS_SHORT_LEG = [
    "10035189396", "10035191048", "10035192870", "10035194059", "10035194311",
]

TOTAL_CONTRACTS = int(sum(qty for _b, _l, _s, qty in COMBO_PARTIAL_FILLS))


# ---------------------------------------------------------------------------
# Window-relative date helpers — never hardcode a calendar date
# ---------------------------------------------------------------------------

def _fill_moment(now: datetime) -> datetime:
    """One day before ``now``: inside the 7-day reconcile window and far older
    than GAP_SLI_MIN_AGE_MINUTES."""
    return now - timedelta(days=1)


def _expiry_dates(now: datetime) -> tuple[str, str]:
    """(iso, compact) expiry, always in the future relative to the run."""
    expiry = _fill_moment(now) + timedelta(days=18)
    return expiry.strftime("%Y-%m-%d"), expiry.strftime("%Y%m%d")


def _occ_local_symbol(root: str, expiry_compact: str, right: str, strike: float) -> str:
    """OCC 21-char local symbol, the form IB puts in ``contract.localSymbol``
    and the Flex importer puts in ``journal.ticker``."""
    return f"{root:<6}{expiry_compact[2:]}{right}{int(round(strike * 1000)):08d}"


def _strike_label(strike: float) -> str:
    """``format_contract``'s strike rendering: int when integral."""
    return str(int(strike)) if float(strike).is_integer() else str(strike)


# ---------------------------------------------------------------------------
# Row builders — shaped by the real writers and verified against Turso
# ---------------------------------------------------------------------------

def _make_db(executed_rows: list[tuple], journal_rows: list[tuple]) -> MagicMock:
    """Stub DB matching the two SELECTs the gap detector issues.

    Honours each statement's ``>= ?`` look-back predicate rather than handing
    back every row unconditionally: ``executed_orders`` filters on ``fill_time``
    (column 3), ``journal`` on ``filled_at`` (column 1), both lexicographic on
    the ISO strings the real writers store. Without this a fix that miscomputes
    the window boundary would ship green.
    """
    db = MagicMock()

    def _execute(sql: str, params: tuple = ()):
        since = str(params[0]) if params else ""
        if "executed_orders" in sql:
            rows = [row for row in executed_rows if str(row[3]) >= since]
        else:
            rows = [row for row in journal_rows if str(row[1]) >= since]
        cursor = MagicMock(spec=["fetchall"])
        cursor.fetchall.return_value = rows
        return cursor

    db.execute.side_effect = _execute
    return db


def _executed_leg_row(
    exec_id: str,
    *,
    root: str,
    strike: float,
    con_id: int,
    side: str,
    qty: float,
    fill_time: str,
    expiry_iso: str,
    expiry_compact: str,
) -> tuple:
    """An ``executed_orders`` row as ``ib_orders.fetch_executed_orders`` writes it.

    ``symbol`` is ``format_contract(contract)`` — the DISPLAY string — and the
    underlying ticker survives only inside ``contract.symbol``.
    """
    payload = {
        "execId": exec_id,
        "account_id": ACCOUNT_ID,
        "permId": PERM_ID,
        "orderId": ORDER_ID,
        "clientId": CLIENT_ID,
        "orderRef": ORDER_REF,
        "symbol": f"{root} C{_strike_label(strike)}",
        "contract": {
            "conId": con_id,
            "symbol": root,
            "secType": "OPT",
            "currency": "USD",
            "multiplier": "100",
            "localSymbol": _occ_local_symbol(root, expiry_compact, "C", strike),
            "tradingClass": root,
            "strike": strike,
            "right": "C",
            "expiry": expiry_iso,
        },
        "side": side,
        "quantity": qty,
        "price": 15.36,
        "avgPrice": 15.2871,
        "cumQty": qty,
        "commission": 1.4467,
        "commissionCurrency": "USD",
        "realizedPNL": 0.0,
        "time": fill_time,
        "exchange": "EMERALD",
    }
    return (exec_id, PERM_ID, json.dumps(payload), fill_time, fill_time)


def _executed_bag_parent_row(exec_id: str, *, root: str, qty: float, fill_time: str) -> tuple:
    """The BAG parent execution IB emits alongside each combo partial fill."""
    payload = {
        "execId": exec_id,
        "account_id": ACCOUNT_ID,
        "permId": PERM_ID,
        "orderId": ORDER_ID,
        "clientId": CLIENT_ID,
        "orderRef": ORDER_REF,
        "symbol": f"{root} Spread",
        "contract": {
            "conId": 0,
            "symbol": root,
            "secType": "BAG",
            "currency": "USD",
            "multiplier": 1,
            "localSymbol": None,
            "tradingClass": None,
            "strike": 0.0,
            "right": "",
            "expiry": None,
        },
        "side": "BOT",
        "quantity": qty,
        "time": fill_time,
        "exchange": "SMART",
    }
    return (exec_id, PERM_ID, json.dumps(payload), fill_time, fill_time)


def _journal_row(
    *,
    ticker: str,
    exec_id: str,
    action: str,
    contracts: int,
    strike: float,
    expiry_compact: str,
    filled_date: str,
) -> tuple:
    """A ``journal`` row in the (payload, filled_at, written_at) column order
    ``_build_journal_coverage`` selects."""
    payload = {
        "ib_exec_id": exec_id,
        "ticker": ticker,
        "action": action,
        "contracts": contracts,
        "strike": strike,
        "right": "C",
        "expiry": expiry_compact,
        "decision": "IB_AUTO_IMPORT",
        "date": filled_date,
    }
    return (json.dumps(payload), filled_date, filled_date)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _combo_executed_rows(now: datetime, *, fill_dt: datetime | None = None) -> list[tuple]:
    """The 15 executed_orders rows: 5 BAG parents + 10 leg fills."""
    fill_dt = fill_dt or _fill_moment(now)
    fill_time = fill_dt.isoformat().replace("+00:00", "Z")
    expiry_iso, expiry_compact = _expiry_dates(now)

    rows: list[tuple] = []
    for bag_id, long_id, short_id, qty in COMBO_PARTIAL_FILLS:
        rows.append(_executed_bag_parent_row(bag_id, root=UNDERLYING, qty=qty, fill_time=fill_time))
        rows.append(
            _executed_leg_row(
                long_id, root=UNDERLYING, strike=LONG_STRIKE, con_id=LONG_CON_ID,
                side="BOT", qty=qty, fill_time=fill_time,
                expiry_iso=expiry_iso, expiry_compact=expiry_compact,
            )
        )
        rows.append(
            _executed_leg_row(
                short_id, root=UNDERLYING, strike=SHORT_STRIKE, con_id=SHORT_CON_ID,
                side="SLD", qty=qty, fill_time=fill_time,
                expiry_iso=expiry_iso, expiry_compact=expiry_compact,
            )
        )
    return rows


def _flex_journal_rows(now: datetime) -> list[tuple]:
    """The two composite journal rows the Flex importer actually wrote:
    OCC local-symbol ticker, '+'-joined Flex-space exec ids."""
    fill_date = _fill_moment(now).strftime("%Y-%m-%d")
    _iso, expiry_compact = _expiry_dates(now)
    return [
        _journal_row(
            ticker=_occ_local_symbol(UNDERLYING, expiry_compact, "C", LONG_STRIKE),
            exec_id="+".join(FLEX_EXEC_IDS_LONG_LEG),
            action="BUY_OPTION",
            contracts=TOTAL_CONTRACTS,
            strike=LONG_STRIKE,
            expiry_compact=expiry_compact,
            filled_date=fill_date,
        ),
        _journal_row(
            ticker=_occ_local_symbol(UNDERLYING, expiry_compact, "C", SHORT_STRIKE),
            exec_id="+".join(FLEX_EXEC_IDS_SHORT_LEG),
            action="SELL_TO_OPEN",
            contracts=TOTAL_CONTRACTS,
            strike=SHORT_STRIKE,
            expiry_compact=expiry_compact,
            filled_date=fill_date,
        ),
    ]


def _leg_exec_ids() -> set[str]:
    ids: set[str] = set()
    for _bag, long_id, short_id, _qty in COMBO_PARTIAL_FILLS:
        ids.add(long_id)
        ids.add(short_id)
    return ids


@pytest.fixture
def now() -> datetime:
    return datetime.now(timezone.utc)


@pytest.fixture
def combo_db(now: datetime) -> MagicMock:
    """The production incident state: 15 executed rows, both legs journaled."""
    return _make_db(_combo_executed_rows(now), _flex_journal_rows(now))


# ---------------------------------------------------------------------------
# Topology guard — the fixture must actually be the multi-leg shape
# ---------------------------------------------------------------------------

class TestComboFixtureTopology:
    def test_each_partial_fill_emits_two_leg_exec_ids_under_one_order(self):
        """`.02.01.01` / `.03.01.01` siblings under a shared exec-id root."""
        for _bag, long_id, short_id, _qty in COMBO_PARTIAL_FILLS:
            long_root, long_leg = long_id.rsplit(".", 3)[0], long_id.split(".")[2]
            short_root, short_leg = short_id.rsplit(".", 3)[0], short_id.split(".")[2]
            assert long_root == short_root
            assert (long_leg, short_leg) == ("02", "03")

    def test_fixture_carries_bag_parents_and_leg_fills(self, now: datetime):
        executed_rows = _combo_executed_rows(now)
        assert len(executed_rows) == len(COMBO_PARTIAL_FILLS) * 3
        assert len(_leg_exec_ids()) == len(COMBO_PARTIAL_FILLS) * 2
        assert len(_flex_journal_rows(now)) == 2

    def test_exec_id_spaces_are_disjoint(self):
        """Exact-match coverage is structurally impossible for these rows: the
        journal carries Flex integers, executed_orders carries IB dotted ids."""
        flex = set(FLEX_EXEC_IDS_LONG_LEG) | set(FLEX_EXEC_IDS_SHORT_LEG)
        assert flex.isdisjoint(_leg_exec_ids())


# ---------------------------------------------------------------------------
# The regression
# ---------------------------------------------------------------------------

class TestFullyJournaledComboIsNotAGap:
    def test_scan_reports_zero_gaps(self, now: datetime, combo_db: MagicMock):
        """Ten leg fills, both legs journaled by the Flex importer — zero gaps."""
        snap = scan_journal_gaps(combo_db, min_age_minutes=GAP_SLI_MIN_AGE_MINUTES, now=now)

        assert snap["executed_orders_scanned"] == len(COMBO_PARTIAL_FILLS) * 3
        assert [g["exec_id"] for g in snap["gaps"]] == []
        assert snap["missing_exec_id_count"] == 0

    def test_no_leg_exec_id_is_reported_missing(self, now: datetime, combo_db: MagicMock):
        snap = scan_journal_gaps(combo_db, min_age_minutes=GAP_SLI_MIN_AGE_MINUTES, now=now)

        assert _leg_exec_ids().isdisjoint({g["exec_id"] for g in snap["gaps"]})

    def test_gap_sli_heartbeats_ok_not_error(self, now: datetime, combo_db: MagicMock):
        """Production symptom #1: the journal-gap-sli row latched state=error."""
        with patch.object(JournalGapSliHandler, "_open_db", return_value=combo_db), \
             patch.object(JournalGapSliHandler, "_now_utc", return_value=now), \
             patch.object(JournalGapSliHandler, "record_cycle_health") as mock_rch:
            result = JournalGapSliHandler().execute()

        state = mock_rch.call_args.args[0]
        detail = mock_rch.call_args.kwargs.get("error") or {}
        assert result["missing_exec_id_count"] == 0
        assert "gap_exec_ids" not in detail
        assert state == "ok"

    def test_reconcile_handler_reports_no_gaps(self, combo_db: MagicMock):
        """Production symptom #2: journal-reconcile latched state=error on the
        SAME ten exec ids, via BaseHandler's swallowed-failure ``error`` key."""
        with patch.object(JournalReconcileHandler, "_open_db", return_value=combo_db):
            result = JournalReconcileHandler().execute()

        assert result["gaps_found"] == 0
        assert "gap_exec_ids" not in result
        assert "error" not in result


class TestTickerNormalisationMechanism:
    """Both halves of the key mismatch that kills the ±1-day fallback."""

    def test_executed_order_contract_key_uses_underlying_not_display_string(self, now: datetime):
        expiry_iso, expiry_compact = _expiry_dates(now)
        fill_time = _fill_moment(now).isoformat().replace("+00:00", "Z")
        _exec_id, _perm, payload_json, _ft, _rt = _executed_leg_row(
            COMBO_PARTIAL_FILLS[0][1],
            root=UNDERLYING, strike=LONG_STRIKE, con_id=LONG_CON_ID,
            side="BOT", qty=3.0, fill_time=fill_time,
            expiry_iso=expiry_iso, expiry_compact=expiry_compact,
        )
        payload = json.loads(payload_json)

        ticker, strike, right, expiry = _extract_contract_key(payload)

        assert (strike, right, expiry) == ("875.0", "C", expiry_compact)
        assert ticker == UNDERLYING  # not "MU C875"

    def test_journal_coverage_key_uses_underlying_not_occ_local_symbol(self, now: datetime):
        fill_date = _fill_moment(now).strftime("%Y-%m-%d")
        _iso, expiry_compact = _expiry_dates(now)
        db = _make_db([], _flex_journal_rows(now)[:1])

        coverage = _build_journal_coverage(db, fill_date)

        assert (UNDERLYING, "875.0", "C", expiry_compact, fill_date) in coverage["contract_dates"]


# ---------------------------------------------------------------------------
# Controls — a fix must not blanket-suppress, and must not regress the path
# that already works
# ---------------------------------------------------------------------------

class TestGenuineComboGapStillDetected:
    def test_unjournaled_leg_is_still_reported(self, now: datetime):
        """Drop the short-leg journal row: those five fills are genuinely absent
        and must still be reported — and the long leg must NOT be."""
        db = _make_db(_combo_executed_rows(now), _flex_journal_rows(now)[:1])

        snap = scan_journal_gaps(db, min_age_minutes=GAP_SLI_MIN_AGE_MINUTES, now=now)

        short_leg_ids = {short_id for _b, _l, short_id, _q in COMBO_PARTIAL_FILLS}
        assert {g["exec_id"] for g in snap["gaps"]} == short_leg_ids
        assert snap["missing_exec_id_count"] == len(short_leg_ids)


class TestFreshFillIsNotYetAGap:
    """The min-age gate keeps a live fill still racing journal_sync out of the
    SLI. Green today; a fix must repair the key, not reach for the age gate."""

    def test_unjournaled_fill_inside_min_age_is_ignored(self, now: datetime):
        fresh = now - timedelta(minutes=GAP_SLI_MIN_AGE_MINUTES // 2)
        db = _make_db(_combo_executed_rows(now, fill_dt=fresh), [])

        snap = scan_journal_gaps(db, min_age_minutes=GAP_SLI_MIN_AGE_MINUTES, now=now)

        assert snap["executed_orders_scanned"] == len(COMBO_PARTIAL_FILLS) * 3
        assert snap["missing_exec_id_count"] == 0


# -- Real-time-written journal rows (production SLV 70/60 call spread) --------

SLV_ROOT = "SLV"
SLV_BAG_EXEC_ID = "0001505f.6a7aa206.01.01"
SLV_LONG_EXEC_ID = "0001d588.6a7b46c5.03.01.01"
SLV_SHORT_EXEC_ID = "0001d588.6a7b46c5.02.01.01"
SLV_LONG_STRIKE = 60.0
SLV_SHORT_STRIKE = 70.0
SLV_QTY = 112.0


def _slv_combo_db(
    now: datetime,
    *,
    long_journal_exec_id: str,
    short_journal_exec_id: str,
) -> MagicMock:
    """One BAG parent + two leg fills, journaled by the REAL-TIME writer.

    ``journal_sync.py:989`` stores ``ticker = contract.symbol``, so the journal
    ticker is the plain underlying "SLV". Callers choose whether the journal's
    exec ids match the executed ones, which selects between the exact-match path
    and the ±1-day per-contract fallback.
    """
    fill_dt = _fill_moment(now)
    fill_time = fill_dt.isoformat().replace("+00:00", "Z")
    fill_date = fill_dt.strftime("%Y-%m-%d")
    expiry_iso, expiry_compact = _expiry_dates(now)

    executed_rows = [
        _executed_bag_parent_row(
            SLV_BAG_EXEC_ID, root=SLV_ROOT, qty=SLV_QTY, fill_time=fill_time
        ),
        _executed_leg_row(
            SLV_LONG_EXEC_ID, root=SLV_ROOT, strike=SLV_LONG_STRIKE, con_id=1,
            side="BOT", qty=SLV_QTY, fill_time=fill_time,
            expiry_iso=expiry_iso, expiry_compact=expiry_compact,
        ),
        _executed_leg_row(
            SLV_SHORT_EXEC_ID, root=SLV_ROOT, strike=SLV_SHORT_STRIKE, con_id=2,
            side="SLD", qty=SLV_QTY, fill_time=fill_time,
            expiry_iso=expiry_iso, expiry_compact=expiry_compact,
        ),
    ]
    journal_rows = [
        _journal_row(
            ticker=SLV_ROOT, exec_id=long_journal_exec_id, action="BUY_OPTION",
            contracts=int(SLV_QTY), strike=SLV_LONG_STRIKE,
            expiry_compact=expiry_compact, filled_date=fill_date,
        ),
        _journal_row(
            ticker=SLV_ROOT, exec_id=short_journal_exec_id, action="SELL_TO_OPEN",
            contracts=int(SLV_QTY), strike=SLV_SHORT_STRIKE,
            expiry_compact=expiry_compact, filled_date=fill_date,
        ),
    ]
    return _make_db(executed_rows, journal_rows)


class TestRealtimeJournaledComboStaysCovered:
    """Plain "SLV" ticker + IB-space leg exec ids that match EXACTLY. Green
    today because ``_find_gaps`` short-circuits on the exec-id set before it
    ever reaches the contract fallback — so this control pins exact-match
    coverage only, and nothing about the ticker key."""

    def test_exact_exec_id_match_keeps_it_at_zero_gaps(self, now: datetime):
        db = _slv_combo_db(
            now,
            long_journal_exec_id=SLV_LONG_EXEC_ID,
            short_journal_exec_id=SLV_SHORT_EXEC_ID,
        )

        snap = scan_journal_gaps(db, min_age_minutes=GAP_SLI_MIN_AGE_MINUTES, now=now)

        assert snap["missing_exec_id_count"] == 0
        assert snap["executed_orders_scanned"] == 3


class TestPlainTickerJournalRowCoversViaContractFallback:
    """The second half of the fix, pinned behaviourally.

    IB re-issues a corrected execution under a bumped trailing segment
    (``.01.01`` -> ``.01.02``); ``journal_rehydrate._drop_superseded_executions``
    exists for exactly that. Here the journal carries the correction id while
    executed_orders still carries the original, so exact match cannot fire and
    the ±1-day per-contract fallback is the only coverage left — against a
    journal ticker of plain "SLV" written by the real-time path.

    This rejects a fix that keys the executed side on ``contract["localSymbol"]``
    (which would repair the Flex/OCC case and leave this one broken) without
    white-box-pinning any particular normalisation target.
    """

    def test_corrected_exec_id_falls_back_to_the_contract_key(self, now: datetime):
        db = _slv_combo_db(
            now,
            long_journal_exec_id=SLV_LONG_EXEC_ID.replace(".01.01", ".01.02"),
            short_journal_exec_id=SLV_SHORT_EXEC_ID.replace(".01.01", ".01.02"),
        )

        snap = scan_journal_gaps(db, min_age_minutes=GAP_SLI_MIN_AGE_MINUTES, now=now)

        assert [g["exec_id"] for g in snap["gaps"]] == []
        assert snap["missing_exec_id_count"] == 0
