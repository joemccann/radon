"""A partial close must NOT change the remaining per-unit basis.

Bug 2026-06-23: covering 500 of a 1000-share MU SHORT dropped the avg entry from
$1,134.97 to $1,093.48 — IB drifts pos.avgCost on a reduce (folds the covered
units' realised P&L into the residual VWAP), and the short came from option
ASSIGNMENT so there is no journal opener to correct it. ib_sync carries the prior
snapshot's per-unit basis forward on a same-side reduce (sticky: holds across
unchanged syncs; never freezes an add/grow, a direction flip, or a position IB
and the snapshot already agree on).
"""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import ib_sync  # noqa: E402


def _mu_collapsed(contracts, ib_avg, direction="SHORT"):
    ec = round(ib_avg * contracts, 2)
    sign = -1 if direction == "SHORT" else 1
    return {
        "ticker": "MU", "structure": f"Stock (-{float(contracts)} shares)", "expiry": "N/A",
        "structure_type": "Stock", "risk_profile": "equity", "contracts": contracts,
        "direction": direction, "entry_cost": sign * ec, "market_value": -526505.0,
        "legs": [{"direction": direction, "contracts": contracts, "type": "Stock", "strike": 0,
                  "avg_cost": ib_avg, "ib_avg_cost": ib_avg, "entry_cost": ec, "market_value": 526505.0}],
    }


def _prior(contracts, per_unit, direction="SHORT"):
    ec = round(per_unit * contracts, 2)
    sign = -1 if direction == "SHORT" else 1
    return {"positions": [{"ticker": "MU", "structure": f"Stock (-{float(contracts)} shares)",
            "expiry": "N/A", "contracts": contracts, "direction": direction,
            "entry_cost": sign * ec, "entry_date": "2026-06-01"}]}


def _convert(monkeypatch, prior, collapsed):
    monkeypatch.setattr(ib_sync, "read_latest_portfolio_snapshot", lambda: prior)
    monkeypatch.setattr(ib_sync, "read_journal_entry_date_maps", lambda: ({}, {}))
    out = ib_sync.convert_to_portfolio_format({"NetLiquidation": 250000}, [collapsed])
    return next(p for p in out["positions"] if p["ticker"] == "MU")


def test_partial_cover_pins_prior_basis(monkeypatch):
    mu = _convert(monkeypatch, _prior(1000, 1134.97), _mu_collapsed(500, 1093.479))
    assert round(mu["entry_cost"], 2) == -567485.0           # 1134.97 × 500, signed
    assert abs(mu["legs"][0]["avg_cost"] - 1134.97) < 0.01    # pinned, not drifted
    assert abs(mu["legs"][0]["ib_avg_cost"] - 1093.479) < 0.01  # raw IB preserved


def test_sticky_holds_when_size_unchanged(monkeypatch):
    # Prior snapshot already pinned at 1134.97; IB still reports drifted 1093.479;
    # size unchanged at 500. Must stay pinned (else it reverts on the next sync).
    mu = _convert(monkeypatch, _prior(500, 1134.97), _mu_collapsed(500, 1093.479))
    assert abs(mu["legs"][0]["avg_cost"] - 1134.97) < 0.01


def test_add_or_grow_keeps_ib_value(monkeypatch):
    # Grew 500 -> 800 on the same side: a genuine add re-blends VWAP — no carry.
    mu = _convert(monkeypatch, _prior(500, 1134.97), _mu_collapsed(800, 1093.479))
    assert abs(mu["legs"][0]["avg_cost"] - 1093.479) < 0.01


def test_no_prior_snapshot_keeps_ib_value(monkeypatch):
    mu = _convert(monkeypatch, {"positions": []}, _mu_collapsed(500, 1093.479))
    assert abs(mu["legs"][0]["avg_cost"] - 1093.479) < 0.01


def test_direction_flip_no_carry(monkeypatch):
    # Prior SHORT, now LONG — a new position, not a reduce.
    mu = _convert(monkeypatch, _prior(1000, 1134.97), _mu_collapsed(500, 1093.479, direction="LONG"))
    assert abs(mu["legs"][0]["avg_cost"] - 1093.479) < 0.01


def test_no_drift_no_carry(monkeypatch):
    # Prior and IB agree (no drift) — leave it; carry-forward must be a no-op.
    mu = _convert(monkeypatch, _prior(1000, 1093.479), _mu_collapsed(500, 1093.479))
    assert abs(mu["legs"][0]["avg_cost"] - 1093.479) < 0.01
