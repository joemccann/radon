"""R-351 / REL-129: every order-placing process on the host respects the halt.

`exit_order_service.py` places a live GTC SELL combo with NEITHER
`is_trading_halted()` nor `check_order_limits()` — the only order-placing
module in the repo with neither guard. `ib_order_manage.py` states the
contract as "every order-placing process on the host", and the halt is the
operator's kill switch. The module is launchd-installable
(`setup_exit_order_service.sh install`) and holds a reserved client id.

The halt file is NEVER touched here: `trading_halt.HALT_FILE` is redirected to
a tmp path, so the production kill switch is neither set nor cleared.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import exit_order_service as svc  # noqa: E402
import trading_halt  # noqa: E402


class _Client:
    """Fails the test if anything reaches the wire."""

    def __init__(self):
        self.placed: list = []

    def qualify_contracts(self, *_a, **_k):
        return None

    def place_order(self, contract, order):
        self.placed.append((contract, order))
        return SimpleNamespace(
            order=SimpleNamespace(orderId=1),
            orderStatus=SimpleNamespace(status="Submitted"),
        )

    def sleep(self, _s):
        return None


LEGS = [
    {"type": "Long Call", "strike": 100.0},
    {"type": "Short Call", "strike": 110.0},
]


@pytest.fixture
def halt_file(tmp_path, monkeypatch):
    """Redirect the kill switch to a tmp path. NEVER the production file."""
    path = tmp_path / "trading_halt.json"
    monkeypatch.setattr(trading_halt, "HALT_FILE", path)
    return path


class TestHaltBlocksPlacement:
    def test_a_set_halt_refuses_the_order(self, halt_file, capsys):
        halt_file.write_text('{"halted": true, "reason": "operator kill switch"}')
        client = _Client()

        result = svc.place_target_order(
            client, "AAPL", LEGS, contracts=1, target_price=5.0
        )

        assert client.placed == [], (
            "a live GTC SELL combo reached IB with the operator's kill switch set"
        )
        assert result is None
        assert "halt" in capsys.readouterr().out.lower()

    def test_an_unreadable_halt_flag_also_refuses(self, halt_file):
        halt_file.write_text("{not json")
        client = _Client()
        assert svc.place_target_order(
            client, "AAPL", LEGS, contracts=1, target_price=5.0
        ) is None
        assert client.placed == []

    def test_no_halt_still_reaches_the_wire(self, halt_file):
        client = _Client()
        svc.place_target_order(client, "AAPL", LEGS, contracts=1, target_price=5.0)
        assert len(client.placed) == 1


class TestOrderLimitsBlockPlacement:
    def test_an_over_cap_quantity_is_refused_before_any_client_call(
        self, halt_file, monkeypatch, capsys
    ):
        import order_limits

        monkeypatch.setenv("RADON_MAX_ORDER_QTY", "10")
        order_limits.max_order_qty.cache_clear() if hasattr(
            order_limits.max_order_qty, "cache_clear"
        ) else None
        client = _Client()

        result = svc.place_target_order(
            client, "AAPL", LEGS, contracts=5_000, target_price=5.0
        )

        assert client.placed == []
        assert result is None
        assert "limit" in capsys.readouterr().out.lower()

    def test_a_within_cap_quantity_is_allowed(self, halt_file, monkeypatch):
        monkeypatch.setenv("RADON_MAX_ORDER_QTY", "10")
        client = _Client()
        svc.place_target_order(client, "AAPL", LEGS, contracts=1, target_price=5.0)
        assert len(client.placed) == 1


def test_dry_run_needs_no_guard_bypass(halt_file):
    """A dry run places nothing, so the guards do not change its behaviour."""
    client = _Client()
    assert svc.place_target_order(
        client, "AAPL", LEGS, contracts=1, target_price=5.0, dry_run=True
    ) == -1
    assert client.placed == []
