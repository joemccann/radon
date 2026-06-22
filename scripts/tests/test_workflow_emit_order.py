"""FU6 (C) — live ``emit_order`` with mandatory confirmation.

``emit_order`` is the order-emitting seam the workflow order node calls. It must:

  - place each row's order through the placement bridge (mocked here — nothing
    hits IB / the network),
  - fire ONLY when the run was explicitly confirmed (the executor's
    ``confirm_order`` gate is preserved: an unconfirmed order node blocks and
    NO order is emitted),
  - keep its per-row placement isolated behind a single module-level seam so a
    test can patch it.

Nothing live: the placement bridge is patched in every test.
"""
from __future__ import annotations

import pytest


class TestEmitOrder:
    def test_emit_order_places_each_row_through_bridge(self, monkeypatch):
        import workflow.nodes as nodes_mod

        placed = []
        monkeypatch.setattr(
            nodes_mod,
            "place_order_via_bridge",
            lambda row, params: placed.append((row, params)),
        )

        rows = [{"ticker": "AAA", "score": 80}, {"ticker": "BBB", "score": 75}]
        params = {"structure": "long_call"}
        nodes_mod.emit_order(rows, params)

        assert [r for r, _ in placed] == rows
        assert all(p == params for _, p in placed)

    def test_emit_order_no_rows_places_nothing(self, monkeypatch):
        import workflow.nodes as nodes_mod

        placed = []
        monkeypatch.setattr(
            nodes_mod,
            "place_order_via_bridge",
            lambda row, params: placed.append(row),
        )

        nodes_mod.emit_order([], {"structure": "long_call"})
        assert placed == []


class TestOrderNodeGate:
    """The confirmation gate is the analogue of OrderRiskGate: an order node
    refuses to emit unless the run carries ``confirm_order=True``."""

    def _graph(self):
        return {
            "nodes": [
                {"id": "n1", "type": "data-source", "params": {"source": "scanner"}},
                {"id": "n2", "type": "order", "params": {"structure": "long_call"}},
            ],
            "edges": [{"from": "n1", "to": "n2"}],
        }

    def test_unconfirmed_run_blocks_and_emits_nothing(self, monkeypatch):
        import workflow.nodes as nodes_mod
        from workflow.executor import execute_graph

        monkeypatch.setattr(
            nodes_mod,
            "run_data_source",
            lambda source, params: [{"ticker": "AAA", "score": 80}],
        )
        placed = []
        monkeypatch.setattr(
            nodes_mod,
            "place_order_via_bridge",
            lambda row, params: placed.append(row),
        )

        report = execute_graph(self._graph())
        assert report.ok is False
        assert report.blocked_by == "n2"
        assert report.requires_confirmation is True
        assert placed == []

    def test_confirmed_run_emits_through_bridge(self, monkeypatch):
        import workflow.nodes as nodes_mod
        from workflow.executor import execute_graph

        monkeypatch.setattr(
            nodes_mod,
            "run_data_source",
            lambda source, params: [{"ticker": "AAA", "score": 80}],
        )
        placed = []
        monkeypatch.setattr(
            nodes_mod,
            "place_order_via_bridge",
            lambda row, params: placed.append(row),
        )

        report = execute_graph(self._graph(), confirm_order=True)
        assert report.ok is True
        assert placed == [{"ticker": "AAA", "score": 80}]


class TestPlacementBridge:
    """``place_order_via_bridge`` is the single seam that maps a row to the
    existing placement path. It builds an order payload from the row and hands
    it to ``run_order_placement`` (the patched IB-placing entrypoint)."""

    def test_bridge_builds_payload_and_calls_placement(self, monkeypatch):
        import workflow.nodes as nodes_mod

        captured = {}
        monkeypatch.setattr(
            nodes_mod,
            "run_order_placement",
            lambda payload: captured.update(payload) or {"status": "ok"},
        )

        row = {
            "ticker": "AAA",
            "action": "BUY",
            "quantity": 3,
            "limit_price": 1.25,
        }
        nodes_mod.place_order_via_bridge(row, {"structure": "long_call"})

        assert captured["symbol"] == "AAA"
        assert captured["action"] == "BUY"
        assert captured["quantity"] == 3
        assert captured["limitPrice"] == 1.25

    def test_bridge_requires_actionable_row(self, monkeypatch):
        import workflow.nodes as nodes_mod

        monkeypatch.setattr(
            nodes_mod,
            "run_order_placement",
            lambda payload: {"status": "ok"},
        )

        with pytest.raises(ValueError):
            nodes_mod.place_order_via_bridge({"score": 80}, {})
