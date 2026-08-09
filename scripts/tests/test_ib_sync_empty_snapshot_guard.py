#!/usr/bin/env python3
"""REL-016 (R-018): empty-snapshot guard in ib_sync.save_portfolio.

A degraded IB session can make fetch_positions return [] and upsert an
EMPTY portfolio snapshot over a non-empty book — exposure/bankroll/basis
carry-forward all read flat. save_portfolio must refuse a zero-position
upsert when the PRIOR snapshot was non-empty, loudly (raise inside the
service_cycle block so the portfolio-sync service_health row records
error), unless explicitly overridden (RADON_ALLOW_EMPTY_SNAPSHOT=1 or
--allow-empty).
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))

import ib_sync  # noqa: E402


class _RecordingCycle:
    """Stands in for db.service_cycle.service_cycle: records whether an
    exception escaped the with-block (the real implementation writes the
    error service_health row exactly in that case)."""

    def __init__(self):
        self.exc_type = None
        self.entered = False

    def __call__(self, service, *, market_hours_class):
        self.service = service
        return self

    def __enter__(self):
        self.entered = True
        return SimpleNamespace(finished_at=None)

    def __exit__(self, exc_type, exc, tb):
        self.exc_type = exc_type
        return False  # re-raise, like the real service_cycle


@pytest.fixture
def harness(monkeypatch):
    import db.service_cycle as service_cycle_mod
    import db.writer as writer_mod

    cycle = _RecordingCycle()
    upserts = []
    monkeypatch.setattr(service_cycle_mod, "service_cycle", cycle)
    monkeypatch.setattr(
        writer_mod, "upsert_portfolio_snapshot",
        lambda taken_at, portfolio: upserts.append(portfolio),
    )
    monkeypatch.setattr(
        writer_mod, "insert_account_margin_sample",
        lambda *a, **k: pytest.fail("margin sample must not be written in these tests"),
    )
    monkeypatch.delenv("RADON_ALLOW_EMPTY_SNAPSHOT", raising=False)
    return SimpleNamespace(cycle=cycle, upserts=upserts, monkeypatch=monkeypatch)


def _set_prior(monkeypatch, snapshot):
    monkeypatch.setattr(ib_sync, "read_latest_portfolio_snapshot", lambda: snapshot)


def _empty_portfolio():
    return {"positions": [], "account_summary": {}}


def _nonempty_portfolio():
    return {
        "positions": [{"ticker": "MU", "structure": "CALL", "contracts": 2}],
        "account_summary": {},
    }


PRIOR_NONEMPTY = {"positions": [{"ticker": "MU"}, {"ticker": "NVDA"}]}


def test_zero_position_upsert_refused_when_prior_nonempty(harness):
    _set_prior(harness.monkeypatch, PRIOR_NONEMPTY)

    with pytest.raises(ib_sync.EmptySnapshotError):
        ib_sync.save_portfolio(_empty_portfolio())

    assert harness.upserts == []


def test_refusal_raises_inside_service_cycle_block(harness):
    """The raise must escape THROUGH the service_cycle context so the real
    implementation writes the portfolio-sync error row."""
    _set_prior(harness.monkeypatch, PRIOR_NONEMPTY)

    with pytest.raises(ib_sync.EmptySnapshotError):
        ib_sync.save_portfolio(_empty_portfolio())

    assert harness.cycle.entered
    assert harness.cycle.exc_type is ib_sync.EmptySnapshotError


def test_genuinely_flat_book_stays_writable_prior_empty(harness):
    _set_prior(harness.monkeypatch, {"positions": []})

    ib_sync.save_portfolio(_empty_portfolio())

    assert len(harness.upserts) == 1


def test_genuinely_flat_book_stays_writable_prior_absent(harness):
    _set_prior(harness.monkeypatch, None)

    ib_sync.save_portfolio(_empty_portfolio())

    assert len(harness.upserts) == 1


def test_prior_read_failure_does_not_block_write(harness):
    def _boom():
        raise RuntimeError("turso down")

    harness.monkeypatch.setattr(ib_sync, "read_latest_portfolio_snapshot", _boom)

    ib_sync.save_portfolio(_empty_portfolio())

    assert len(harness.upserts) == 1


def test_positions_present_unchanged_behavior(harness):
    _set_prior(harness.monkeypatch, PRIOR_NONEMPTY)

    ib_sync.save_portfolio(_nonempty_portfolio())

    assert len(harness.upserts) == 1
    assert harness.cycle.exc_type is None


def test_env_override_allows_empty_upsert(harness):
    _set_prior(harness.monkeypatch, PRIOR_NONEMPTY)
    harness.monkeypatch.setenv("RADON_ALLOW_EMPTY_SNAPSHOT", "1")

    ib_sync.save_portfolio(_empty_portfolio())

    assert len(harness.upserts) == 1


def test_allow_empty_kwarg_allows_empty_upsert(harness):
    _set_prior(harness.monkeypatch, PRIOR_NONEMPTY)

    ib_sync.save_portfolio(_empty_portfolio(), allow_empty=True)

    assert len(harness.upserts) == 1


def test_cli_exposes_allow_empty_flag():
    """--allow-empty must parse (wired to save_portfolio's override)."""
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--allow-empty", action="store_true")
    args = parser.parse_args(["--allow-empty"])
    assert args.allow_empty

    source = Path(ib_sync.__file__).read_text()
    assert '"--allow-empty"' in source
    assert "allow_empty=args.allow_empty" in source
