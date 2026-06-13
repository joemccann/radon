#!/usr/bin/env python3
"""
Targeted regression tests for the low-coverage dark branches in
scripts/clients/journal_basis.py.

Branch map:
  B6-dict   _row_value DICT branch (CTA-01 bug path)
  B6-tuple  _row_value TUPLE/LIST branch (existing happy path, confirmed here)
  B6-attr   _row_value attribute-fallback branch (SimpleNamespace rows)
  B7-dict   _payload_from_row when payload column is already a dict
  B7-json   _payload_from_row with valid JSON string payload
  B7-bad    _payload_from_row with invalid-JSON payload → returns {}
  B7-scalar _payload_from_row when payload is an int/None → returns {}
  B8-6dig   _normalize_expiry: 6-digit '260717' → '20260717'
  B8-8dig   _normalize_expiry: 8-digit '20260717' → unchanged
  B8-bad    _normalize_expiry: garbage → ''
  B9-uk     _signed_qty unknown action → 0.0
  B9-closed _signed_qty 'CLOSED' → negative
  B9-short  _signed_qty 'SHORT' → negative
  B9-zero   _signed_qty qty<=0 guard → 0.0
  B10-stk   prior_net_qty_for_contract STK path (ticker-only, no strike/right)
  B10-opt   prior_net_qty_for_contract OPT path (bucket-key match)
  B10-optsk prior_net_qty_for_contract OPT mislabel scenario (SELL_OPTION vs SELL_TO_OPEN)
  B10-acc   prior_net_qty_for_contract net accumulation across multiple rows
  B10-bad   prior_net_qty_for_contract bad bucket key → 0.0
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))

from clients.journal_basis import (  # noqa: E402
    _normalize_expiry,
    _payload_from_row,
    _row_value,
    _signed_qty,
    compute_open_basis_for_ticker,
    prior_net_qty_for_contract,
)

# ---------------------------------------------------------------------------
# Helpers — driver-faithful cursor doubles (CTA-01 lesson)
# ---------------------------------------------------------------------------

_JOURNAL_COLUMNS = ("payload", "filled_at", "written_at")


def _tuple_row(payload: dict, filled_at: str = "2026-01-01") -> tuple:
    """Real libsql_experimental cursor row: plain tuple in SELECT column order."""
    return (json.dumps(payload), filled_at, filled_at)


def _dict_row(payload: dict, filled_at: str = "2026-01-01") -> dict:
    """Future libsql dict-row path (CTA-01 B6 bug path)."""
    return {
        "payload": json.dumps(payload),
        "filled_at": filled_at,
        "written_at": filled_at,
    }


def _attr_row(payload: dict, filled_at: str = "2026-01-01"):
    """Attribute-access row (e.g. sqlite3.Row or SimpleNamespace)."""
    return SimpleNamespace(
        payload=json.dumps(payload),
        filled_at=filled_at,
        written_at=filled_at,
    )


class _FakeCursor:
    """Mirrors libsql_experimental 0.0.55: rows via fetchall(), no .rows."""

    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _FakeDb:
    def __init__(self, rows):
        self._rows = rows
        self.calls = []

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        return _FakeCursor(self._rows)


# ---------------------------------------------------------------------------
# B6 — _row_value
# ---------------------------------------------------------------------------


class TestRowValue:
    """_row_value must return identical results regardless of row type."""

    _payload = json.dumps({"ticker": "AAPL"})
    _filled_at = "2026-06-01"
    _written_at = "2026-06-01"

    def _make_tuple(self):
        return (self._payload, self._filled_at, self._written_at)

    def _make_dict(self):
        return {
            "payload": self._payload,
            "filled_at": self._filled_at,
            "written_at": self._written_at,
        }

    def _make_attr(self):
        return SimpleNamespace(
            payload=self._payload,
            filled_at=self._filled_at,
            written_at=self._written_at,
        )

    def test_tuple_row_payload(self):
        assert _row_value(self._make_tuple(), "payload") == self._payload

    def test_tuple_row_filled_at(self):
        assert _row_value(self._make_tuple(), "filled_at") == self._filled_at

    def test_dict_row_payload(self):
        """B6-dict: CTA-01 bug path — dict rows must resolve by name."""
        assert _row_value(self._make_dict(), "payload") == self._payload

    def test_dict_row_filled_at(self):
        assert _row_value(self._make_dict(), "filled_at") == self._filled_at

    def test_dict_row_missing_key(self):
        assert _row_value(self._make_dict(), "nonexistent") is None

    def test_attr_row_payload(self):
        """B6-attr: attribute-fallback branch (SimpleNamespace / sqlite3.Row)."""
        assert _row_value(self._make_attr(), "payload") == self._payload

    def test_attr_row_missing_key(self):
        assert _row_value(self._make_attr(), "nonexistent") is None

    def test_tuple_and_dict_return_identical_values(self):
        """Driver-faithful parity: tuple vs dict rows must be indistinguishable."""
        for key in ("payload", "filled_at", "written_at"):
            assert _row_value(self._make_tuple(), key) == _row_value(
                self._make_dict(), key
            ), f"mismatch for key '{key}'"

    def test_tuple_invalid_key_returns_none(self):
        """B6-tuple: unknown column name → IndexError catch → None."""
        assert _row_value(self._make_tuple(), "unknown_col") is None

    def test_list_row_same_as_tuple(self):
        """list rows behave identically to tuple rows."""
        row = list(self._make_tuple())
        assert _row_value(row, "payload") == self._payload


# ---------------------------------------------------------------------------
# B7 — _payload_from_row
# ---------------------------------------------------------------------------


class TestPayloadFromRow:
    def test_string_payload_parsed(self):
        """B7-json: valid JSON string → dict."""
        payload = {"ticker": "SPY", "action": "BUY_OPTION"}
        row = _tuple_row(payload)
        result = _payload_from_row(row)
        assert result == payload

    def test_dict_payload_returned_directly(self):
        """B7-dict: payload column already a dict (future driver behaviour)."""
        raw = {"ticker": "SPY", "action": "BUY_OPTION"}
        # Construct a row where the payload column IS a dict (not serialised).
        row = (raw, "2026-01-01", "2026-01-01")
        result = _payload_from_row(row)
        assert result is raw  # must be the same object (no re-serialise)

    def test_invalid_json_returns_empty_dict(self):
        """B7-bad: malformed JSON string → returns {} (not raises)."""
        row = ("{not valid json!!", "2026-01-01", "2026-01-01")
        assert _payload_from_row(row) == {}

    def test_json_non_dict_returns_empty_dict(self):
        """B7-bad: valid JSON but not a dict (e.g. list) → returns {}."""
        row = ("[1, 2, 3]", "2026-01-01", "2026-01-01")
        assert _payload_from_row(row) == {}

    def test_none_payload_returns_empty_dict(self):
        """B7-scalar: None payload column → returns {}."""
        row = (None, "2026-01-01", "2026-01-01")
        assert _payload_from_row(row) == {}

    def test_int_payload_returns_empty_dict(self):
        """B7-scalar: numeric payload column → returns {}."""
        row = (42, "2026-01-01", "2026-01-01")
        assert _payload_from_row(row) == {}

    def test_dict_and_string_rows_give_same_payload(self):
        """Driver-faithful parity: dict row vs tuple row for the same payload."""
        payload = {"ticker": "GLD", "action": "SELL_OPTION", "contracts": 10}
        tuple_row = _tuple_row(payload)
        dict_row = _dict_row(payload)
        assert _payload_from_row(tuple_row) == _payload_from_row(dict_row)


# ---------------------------------------------------------------------------
# B8 — _normalize_expiry
# ---------------------------------------------------------------------------


class TestNormalizeExpiry:
    def test_6_digit_prepends_20(self):
        """B8-6dig: '260717' → '20260717'."""
        assert _normalize_expiry("260717") == "20260717"

    def test_8_digit_unchanged(self):
        """B8-8dig: '20260717' → '20260717'."""
        assert _normalize_expiry("20260717") == "20260717"

    def test_garbage_returns_empty(self):
        """B8-bad: non-digit garbage → ''."""
        assert _normalize_expiry("garbage") == ""

    def test_none_returns_empty(self):
        assert _normalize_expiry(None) == ""

    def test_mixed_format_6_digit_with_slashes(self):
        """Digits are extracted then length-checked: '26/07/17' has 6 digits → '20260717'."""
        assert _normalize_expiry("26/07/17") == "20260717"

    def test_wrong_digit_count_returns_empty(self):
        """5 digits → neither 6 nor 8 → ''."""
        assert _normalize_expiry("26071") == ""

    def test_int_8_digit(self):
        """Integer input with 8 digits is valid."""
        assert _normalize_expiry(20260717) == "20260717"

    def test_int_6_digit(self):
        """Integer input with 6 digits gets '20' prefix."""
        assert _normalize_expiry(260717) == "20260717"


# ---------------------------------------------------------------------------
# B9 — _signed_qty
# ---------------------------------------------------------------------------


class TestSignedQty:
    def test_buy_positive(self):
        assert _signed_qty("BUY_OPTION", 5) == pytest.approx(5.0)

    def test_buy_to_open_positive(self):
        assert _signed_qty("BUY_TO_OPEN", 3) == pytest.approx(3.0)

    def test_sell_negative(self):
        assert _signed_qty("SELL_OPTION", 10) == pytest.approx(-10.0)

    def test_sell_to_open_negative(self):
        assert _signed_qty("SELL_TO_OPEN", 7) == pytest.approx(-7.0)

    def test_short_negative(self):
        """B9-short: SHORT prefix → negative signed qty."""
        assert _signed_qty("SHORT", 4) == pytest.approx(-4.0)

    def test_short_sell_negative(self):
        """SHORT_SELL prefix also matches the SHORT branch."""
        assert _signed_qty("SHORT_SELL", 2) == pytest.approx(-2.0)

    def test_closed_negative(self):
        """B9-closed: exact 'CLOSED' label (case-insensitive) → negative."""
        assert _signed_qty("CLOSED", 8) == pytest.approx(-8.0)
        assert _signed_qty("closed", 8) == pytest.approx(-8.0)

    def test_unknown_action_returns_zero(self):
        """B9-uk: unrecognised label → 0.0 (no implicit sign)."""
        assert _signed_qty("UNKNOWN_ACTION", 5) == pytest.approx(0.0)

    def test_empty_action_returns_zero(self):
        assert _signed_qty("", 5) == pytest.approx(0.0)

    def test_none_action_returns_zero(self):
        assert _signed_qty(None, 5) == pytest.approx(0.0)

    def test_zero_qty_guard(self):
        """B9-zero: qty <= 0 short-circuits regardless of action."""
        assert _signed_qty("BUY_OPTION", 0) == pytest.approx(0.0)
        assert _signed_qty("BUY_OPTION", -3) == pytest.approx(0.0)
        assert _signed_qty("SELL_OPTION", 0) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# B10 — prior_net_qty_for_contract
# ---------------------------------------------------------------------------


class TestPriorNetQtyForContract:
    """Cover the SELL_OPTION-vs-SELL_TO_OPEN labeller decision surface."""

    # ------------------------------------------------------------------
    # STK branch
    # ------------------------------------------------------------------

    def _stk_payload(self, action: str, shares: float) -> dict:
        return {"ticker": "TSLA", "action": action, "shares": shares}

    def test_stk_buy_single_row(self):
        """B10-stk: STK path — matches on ticker only, no option fields."""
        rows = [_tuple_row(self._stk_payload("BUY", 100))]
        db = _FakeDb(rows)
        qty = prior_net_qty_for_contract(db, ticker="TSLA", sec_type="STK")
        assert qty == pytest.approx(100.0)

    def test_stk_sell_single_row(self):
        rows = [_tuple_row(self._stk_payload("SELL", 50))]
        db = _FakeDb(rows)
        qty = prior_net_qty_for_contract(db, ticker="TSLA", sec_type="STK")
        assert qty == pytest.approx(-50.0)

    def test_stk_net_accumulation(self):
        """B10-acc: STK with multiple rows accumulates net qty."""
        rows = [
            _tuple_row(self._stk_payload("BUY", 200)),
            _tuple_row(self._stk_payload("SELL", 75)),
        ]
        db = _FakeDb(rows)
        qty = prior_net_qty_for_contract(db, ticker="TSLA", sec_type="STK")
        # net = 200 + (−75) = 125
        assert qty == pytest.approx(125.0)

    def test_stk_skips_option_looking_rows(self):
        """B10-stk: rows with strike/right fields are skipped in STK mode."""
        rows = [
            _tuple_row(self._stk_payload("BUY", 100)),
            _tuple_row(
                {
                    "ticker": "TSLA",
                    "action": "BUY_OPTION",
                    "contracts": 5,
                    "strike": 250,
                    "right": "C",
                    "expiry": "20261218",
                }
            ),
        ]
        db = _FakeDb(rows)
        qty = prior_net_qty_for_contract(db, ticker="TSLA", sec_type="STK")
        # Option row must be skipped; only the 100-share buy counts.
        assert qty == pytest.approx(100.0)

    def test_stk_dict_rows_identical_to_tuple_rows(self):
        """Driver-faithful parity: dict cursor must give the same net qty."""
        rows_tuple = [_tuple_row(self._stk_payload("BUY", 100))]
        rows_dict = [_dict_row(self._stk_payload("BUY", 100))]
        db_t = _FakeDb(rows_tuple)
        db_d = _FakeDb(rows_dict)
        assert prior_net_qty_for_contract(
            db_t, ticker="TSLA", sec_type="STK"
        ) == prior_net_qty_for_contract(db_d, ticker="TSLA", sec_type="STK")

    # ------------------------------------------------------------------
    # OPT branch
    # ------------------------------------------------------------------

    def _opt_payload(self, action: str, contracts: float, right: str = "C") -> dict:
        return {
            "ticker": "AAOI",
            "action": action,
            "contracts": contracts,
            "strike": 200,
            "right": right,
            "expiry": "20260717",
        }

    def test_opt_buy_single_row(self):
        """B10-opt: OPT path, single BUY row."""
        rows = [_tuple_row(self._opt_payload("BUY_OPTION", 50))]
        db = _FakeDb(rows)
        qty = prior_net_qty_for_contract(
            db,
            ticker="AAOI",
            sec_type="OPT",
            strike=200,
            right="C",
            expiry="20260717",
        )
        assert qty == pytest.approx(50.0)

    def test_opt_sell_to_open_negative(self):
        """B10-opt: SELL_TO_OPEN → negative net (short open)."""
        rows = [_tuple_row(self._opt_payload("SELL_TO_OPEN", 25))]
        db = _FakeDb(rows)
        qty = prior_net_qty_for_contract(
            db,
            ticker="AAOI",
            sec_type="OPT",
            strike=200,
            right="C",
            expiry="20260717",
        )
        assert qty == pytest.approx(-25.0)

    def test_opt_buy_then_partial_sell_net(self):
        """B10-acc: BUY 50 then SELL 25 → net +25 (labels SELL_OPTION, closing)."""
        rows = [
            _tuple_row(self._opt_payload("BUY_OPTION", 50)),
            _tuple_row(self._opt_payload("SELL_OPTION", 25)),
        ]
        db = _FakeDb(rows)
        qty = prior_net_qty_for_contract(
            db,
            ticker="AAOI",
            sec_type="OPT",
            strike=200,
            right="C",
            expiry="20260717",
        )
        # 50 − 25 = 25
        assert qty == pytest.approx(25.0)

    def test_opt_mislabel_sell_to_open_as_close(self):
        """B10-optsk: 2026-05-22 mislabel class.

        The daily importer sometimes labels a SELL close as SELL_TO_OPEN.
        prior_net_qty_for_contract must accumulate sign faithfully so the
        real-time fill writer can compare and choose the correct label.

        Fixture: BUY 50 then two 'SELL_TO_OPEN' rows of 25 each (mislabelled closes).
        Expected net = 50 − 25 − 25 = 0 (fully closed).
        """
        rows = [
            _tuple_row(self._opt_payload("BUY_OPTION", 50)),
            _tuple_row(self._opt_payload("SELL_TO_OPEN", 25)),  # mislabelled close
            _tuple_row(self._opt_payload("SELL_TO_OPEN", 25)),  # mislabelled close
        ]
        db = _FakeDb(rows)
        qty = prior_net_qty_for_contract(
            db,
            ticker="AAOI",
            sec_type="OPT",
            strike=200,
            right="C",
            expiry="20260717",
        )
        assert qty == pytest.approx(0.0)

    def test_opt_different_right_is_excluded(self):
        """OPT lookup is bucket-scoped: a PUT row is not counted for the CALL query."""
        rows = [
            _tuple_row(self._opt_payload("BUY_OPTION", 50, right="C")),
            _tuple_row(self._opt_payload("BUY_OPTION", 10, right="P")),  # different bucket
        ]
        db = _FakeDb(rows)
        qty = prior_net_qty_for_contract(
            db,
            ticker="AAOI",
            sec_type="OPT",
            strike=200,
            right="C",
            expiry="20260717",
        )
        assert qty == pytest.approx(50.0)

    def test_opt_dict_rows_identical_to_tuple_rows(self):
        """Driver-faithful parity for OPT path."""
        rows_tuple = [_tuple_row(self._opt_payload("BUY_OPTION", 30))]
        rows_dict = [_dict_row(self._opt_payload("BUY_OPTION", 30))]
        db_t = _FakeDb(rows_tuple)
        db_d = _FakeDb(rows_dict)
        result_t = prior_net_qty_for_contract(
            db_t,
            ticker="AAOI",
            sec_type="OPT",
            strike=200,
            right="C",
            expiry="20260717",
        )
        result_d = prior_net_qty_for_contract(
            db_d,
            ticker="AAOI",
            sec_type="OPT",
            strike=200,
            right="C",
            expiry="20260717",
        )
        assert result_t == result_d

    def test_opt_closed_action_counts_as_negative(self):
        """B9-closed inside B10-opt: 'CLOSED' label reduces net."""
        rows = [
            _tuple_row(self._opt_payload("BUY_OPTION", 10)),
            _tuple_row(self._opt_payload("CLOSED", 10)),
        ]
        db = _FakeDb(rows)
        qty = prior_net_qty_for_contract(
            db,
            ticker="AAOI",
            sec_type="OPT",
            strike=200,
            right="C",
            expiry="20260717",
        )
        assert qty == pytest.approx(0.0)

    def test_opt_short_action_counts_as_negative(self):
        """B9-short inside B10-opt: 'SHORT' label opens short."""
        rows = [_tuple_row(self._opt_payload("SHORT", 5))]
        db = _FakeDb(rows)
        qty = prior_net_qty_for_contract(
            db,
            ticker="AAOI",
            sec_type="OPT",
            strike=200,
            right="C",
            expiry="20260717",
        )
        assert qty == pytest.approx(-5.0)

    def test_opt_bad_bucket_key_returns_zero(self):
        """B10-bad: missing expiry means bucket key cannot be formed → 0.0."""
        db = _FakeDb([])  # irrelevant — should bail before querying
        qty = prior_net_qty_for_contract(
            db,
            ticker="AAOI",
            sec_type="OPT",
            strike=200,
            right="C",
            expiry=None,  # cannot normalise → bucket key is None
        )
        assert qty == pytest.approx(0.0)

    def test_opt_6_digit_expiry_normalised_correctly(self):
        """B8-6dig inside B10-opt: '260717' expiry matches '20260717' rows."""
        rows = [_tuple_row(self._opt_payload("BUY_OPTION", 15))]
        db = _FakeDb(rows)
        # Query uses 6-digit form; row contains 8-digit form — they normalise identically.
        qty = prior_net_qty_for_contract(
            db,
            ticker="AAOI",
            sec_type="OPT",
            strike=200,
            right="C",
            expiry="260717",  # 6-digit input → '20260717' after normalisation
        )
        assert qty == pytest.approx(15.0)

    def test_empty_ticker_returns_zero(self):
        """Guard: empty ticker short-circuits before any DB call."""
        db = _FakeDb([])
        qty = prior_net_qty_for_contract(db, ticker="", sec_type="OPT")
        assert qty == pytest.approx(0.0)
        assert db.calls == []

    def test_opt_unknown_action_skips_row(self):
        """B9-uk inside prior_net_qty: row with unknown action contributes 0 to net."""
        rows = [
            _tuple_row(self._opt_payload("BUY_OPTION", 10)),
            _tuple_row(self._opt_payload("MYSTERY_FILL", 5)),  # unknown → 0 contribution
        ]
        db = _FakeDb(rows)
        qty = prior_net_qty_for_contract(
            db,
            ticker="AAOI",
            sec_type="OPT",
            strike=200,
            right="C",
            expiry="20260717",
        )
        # Only the BUY contributes.
        assert qty == pytest.approx(10.0)

    def test_opt_uses_shares_fallback_when_contracts_missing(self):
        """qty_raw falls back to 'shares' when 'contracts' key absent (STK-style row)."""
        payload = {
            "ticker": "AAOI",
            "action": "BUY_OPTION",
            "shares": 20,  # 'contracts' key absent
            "strike": 200,
            "right": "C",
            "expiry": "20260717",
        }
        rows = [_tuple_row(payload)]
        db = _FakeDb(rows)
        qty = prior_net_qty_for_contract(
            db,
            ticker="AAOI",
            sec_type="OPT",
            strike=200,
            right="C",
            expiry="20260717",
        )
        assert qty == pytest.approx(20.0)
