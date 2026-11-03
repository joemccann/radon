"""Basis carry-forward must not pin a closed lot's basis (audit §3.1, C5).

The same-side carry kept the prior snapshot's per-unit basis whenever the
live position was no larger on the same side and IB's avgCost differed.
A close and reopen between syncs satisfies both, so the new lot inherited
the old lot's basis. A measured basis (journal / session fills) was also
overridden whenever it agreed with IB, the carried leg still claimed
``basis_source`` from IB, and a malformed prior row was swallowed silently.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import ib_sync

T0 = "2026-11-03T15:00:00+00:00"


def _stock(contracts, ib_avg, *, basis_source="ib"):
    ec = round(ib_avg * contracts, 2)
    return {
        "ticker": "MU", "structure": f"Stock (-{float(contracts)} shares)", "expiry": "N/A",
        "structure_type": "Stock", "risk_profile": "equity", "contracts": contracts,
        "direction": "SHORT", "entry_cost": -ec, "market_value": -500000.0,
        "legs": [{"direction": "SHORT", "contracts": contracts, "type": "Stock", "strike": 0,
                  "avg_cost": ib_avg, "ib_avg_cost": ib_avg, "entry_cost": ec,
                  "basis_source": basis_source, "market_value": 500000.0}],
    }


def _prior(contracts, per_unit, *, last_sync=T0, structure=None):
    return {
        "last_sync": last_sync,
        "positions": [{"ticker": "MU", "structure": structure or f"Stock (-{float(contracts)} shares)",
                       "expiry": "N/A", "contracts": contracts, "direction": "SHORT",
                       "entry_cost": -round(per_unit * contracts, 2), "entry_date": "2026-06-01"}],
    }


def _fill(side, shares, minute):
    return SimpleNamespace(
        contract=SimpleNamespace(symbol="MU", secType="STK"),
        execution=SimpleNamespace(side=side, shares=shares,
                                  time=datetime(2026, 11, 3, 15, minute, tzinfo=timezone.utc)),
    )


def _convert(monkeypatch, prior, collapsed, fills=None):
    monkeypatch.setattr(ib_sync, "read_latest_portfolio_snapshot", lambda: prior)
    monkeypatch.setattr(ib_sync, "read_journal_entry_date_maps", lambda: ({}, {}))
    out = ib_sync.convert_to_portfolio_format(
        {"NetLiquidation": 250000}, [collapsed], session_fills=fills
    )
    return out["positions"][0]


def test_close_and_reopen_between_syncs_keeps_the_new_lot_basis(monkeypatch):
    fills = [_fill("BOT", 1000, 5), _fill("SLD", 500, 10)]
    mu = _convert(monkeypatch, _prior(1000, 1134.97), _stock(500, 1000.0), fills)
    assert mu["legs"][0]["avg_cost"] == 1000.0


def test_partial_cover_after_the_snapshot_still_carries(monkeypatch):
    fills = [_fill("SLD", 1000, 0) , _fill("BOT", 500, 5)]  # first fill predates the snapshot
    fills[0].execution.time = datetime(2026, 11, 3, 14, 0, tzinfo=timezone.utc)
    mu = _convert(monkeypatch, _prior(1000, 1134.97), _stock(500, 1093.479), fills)
    assert abs(mu["legs"][0]["avg_cost"] - 1134.97) < 0.01
    assert mu["legs"][0]["basis_source"] == "carried_forward"


def test_measured_journal_basis_is_never_overridden(monkeypatch):
    mu = _convert(monkeypatch, _prior(1000, 1134.97), _stock(500, 1093.479, basis_source="journal"))
    assert mu["legs"][0]["avg_cost"] == 1093.479
    assert mu["legs"][0]["basis_source"] == "journal"


def test_malformed_prior_row_is_reported(monkeypatch, capsys):
    prior = _prior(1000, 1134.97)
    prior["positions"][0]["contracts"] = "garbage"
    mu = _convert(monkeypatch, prior, _stock(500, 1093.479))
    assert mu["legs"][0]["avg_cost"] == 1093.479
    assert "basis carry" in capsys.readouterr().err
