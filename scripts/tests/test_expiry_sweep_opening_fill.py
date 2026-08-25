"""REL-064 / R-155 (P1) — the R-074 `>=` guard now matches the OPENING fill.

`last_date` is `max(payload["date"][:10])` across ALL journal rows for the
contract, openers included. The guard was widened from `>` to `>=` so a
CLOSING fill booked on expiry day counts as handled — but an opening fill
booked on expiry day satisfies exactly the same condition, `_is_guarded`
returns True, the sweep skips, and the expiration is never written. Same
over-match on the executed-orders leg.

Every 0DTE that expires worthless then stays permanently open in the journal:
`/journal`, `/portfolio` and open-basis all carry a phantom position and
realized P&L never books the loss. The sweep is the only mechanism that would
write it; recovery is by hand.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from monitor_daemon.handlers import expiry_sweep as sweep

EXPIRY = "2026-08-21"


def _journal_row(action: str, day: str, contracts: int = 1) -> dict:
    return {
        "ticker": "SPY",
        "action": action,
        "contracts": contracts,
        "date": f"{day}T14:31:00Z",
        "expiry": EXPIRY.replace("-", ""),
        "strike": 640.0,
        "right": "C",
    }


def _candidate(rows: list[dict]) -> dict:
    state = sweep._aggregate_contracts(rows)[sweep._contract_key(rows[0])]
    return {**state, "expiry_date": sweep._parse_expiry(rows[0]["expiry"])}


class TestOpeningFillOnExpiryDay:
    def test_a_0dte_opened_on_expiry_day_is_still_swept(self):
        """The whole finding: a BUY_OPTION booked on expiry day is the OPEN,
        not evidence the contract was handled."""
        rows = [_journal_row("BUY_OPTION", EXPIRY)]
        candidate = _candidate(rows)

        assert candidate["net"] == 1
        assert sweep.ExpirySweepHandler._is_guarded(candidate, []) is False

    def test_a_closing_fill_on_expiry_day_still_guards(self):
        """R-074's case must keep working: a SELL that flattens the position
        on expiry day demonstrably handled it."""
        rows = [
            _journal_row("BUY_OPTION", "2026-08-14"),
            _journal_row("SELL_OPTION", EXPIRY),
        ]
        candidate = _candidate(rows)

        assert candidate["net"] == 0
        assert sweep.ExpirySweepHandler._is_guarded(candidate, []) is True

    def test_a_partial_close_on_expiry_day_still_guards_conservatively(self):
        """A REDUCING fill on expiry day keeps guarding even when a residual
        remains. That residual arguably should be swept, but writing a $0.00
        expiration next to a same-day close is the double-booking this guard
        exists to prevent, and R-155 is only about the OPENING fill. Filed as
        NF-7 rather than changed here."""
        rows = [
            _journal_row("BUY_OPTION", "2026-08-14", contracts=10),
            _journal_row("SELL_OPTION", EXPIRY, contracts=4),
        ]
        candidate = _candidate(rows)

        assert candidate["net"] == 6
        assert sweep.ExpirySweepHandler._is_guarded(candidate, []) is True

    def test_an_opening_executed_order_on_expiry_day_does_not_guard(self):
        rows = [_journal_row("BUY_OPTION", EXPIRY)]
        candidate = _candidate(rows)
        executed = [
            {
                "payload": {
                    "contract": {
                        "symbol": "SPY",
                        "expiry": EXPIRY.replace("-", ""),
                        "strike": 640.0,
                        "right": "C",
                    },
                    "action": "BUY",
                },
                "fill_date": EXPIRY,
            }
        ]

        assert sweep.ExpirySweepHandler._is_guarded(candidate, executed) is False

    def test_a_closing_executed_order_on_expiry_day_guards(self):
        rows = [_journal_row("BUY_OPTION", "2026-08-14")]
        candidate = _candidate(rows)
        executed = [
            {
                "payload": {
                    "contract": {
                        "symbol": "SPY",
                        "expiry": EXPIRY.replace("-", ""),
                        "strike": 640.0,
                        "right": "C",
                    },
                    "action": "SELL",
                },
                "fill_date": EXPIRY,
            }
        ]

        assert sweep.ExpirySweepHandler._is_guarded(candidate, executed) is True
