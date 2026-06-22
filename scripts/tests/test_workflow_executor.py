"""F14 — Visual flow-pipeline node-graph composer (backend).

Behaviours pinned (red first, then green):
  - the expression engine evaluates arithmetic / comparison / boolean
    expressions over a context dict and refuses unsafe names
  - a data-source node wraps an existing scanner/UW fetcher (mocked — never
    hits UW/IB) and writes its result into the run context
  - a filter/expression node keeps only rows whose predicate is truthy
  - nodes execute in dependency order (data-source -> filter -> notify)
  - the gate node REUSES the shared Four-Gates contract and BLOCKS the graph
    when convexity (gain >= 2x loss) or Kelly (positive edge) fails
  - an order-emitting node refuses to fire without explicit confirmation
    (OrderRiskGate is the mandatory confirmation seam)
  - the notify node routes through the EXISTING F6 dispatcher (mocked)
"""
from __future__ import annotations

import pytest


# ── expression engine ───────────────────────────────────────────────

class TestExpressionEngine:
    def test_arithmetic(self):
        from workflow.expression import evaluate_expression

        assert evaluate_expression("a + b * 2", {"a": 1, "b": 3}) == 7

    def test_comparison_and_boolean(self):
        from workflow.expression import evaluate_expression

        ctx = {"score": 80, "direction": "ACCUMULATION"}
        assert evaluate_expression("score >= 60 and direction == 'ACCUMULATION'", ctx) is True
        assert evaluate_expression("score < 60", ctx) is False

    def test_missing_name_raises(self):
        from workflow.expression import ExpressionError, evaluate_expression

        with pytest.raises(ExpressionError):
            evaluate_expression("unknown_metric > 1", {"score": 1})

    def test_unsafe_call_is_refused(self):
        from workflow.expression import ExpressionError, evaluate_expression

        with pytest.raises(ExpressionError):
            evaluate_expression("__import__('os').system('echo hi')", {})

    def test_attribute_access_is_refused(self):
        from workflow.expression import ExpressionError, evaluate_expression

        with pytest.raises(ExpressionError):
            evaluate_expression("a.__class__", {"a": 1})


# ── shared Four-Gates contract ──────────────────────────────────────

class TestGates:
    def test_convexity_passes_when_gain_at_least_2x_loss(self):
        from workflow.gates import convexity_gate

        result = convexity_gate(max_gain=300.0, max_loss=100.0)
        assert result.passed is True

    def test_convexity_fails_when_gain_below_2x_loss(self):
        from workflow.gates import convexity_gate

        result = convexity_gate(max_gain=150.0, max_loss=100.0)
        assert result.passed is False
        assert result.gate == "convexity"

    def test_kelly_gate_reuses_kelly_module_and_blocks_no_edge(self):
        from workflow.gates import kelly_gate

        # prob 0.3 at even odds → negative full kelly → no edge → block
        result = kelly_gate(prob_win=0.3, odds=1.0)
        assert result.passed is False
        assert result.gate == "risk"

    def test_kelly_gate_passes_with_edge(self):
        from workflow.gates import kelly_gate

        result = kelly_gate(prob_win=0.7, odds=2.0)
        assert result.passed is True


# ── registry ────────────────────────────────────────────────────────

class TestRegistry:
    def test_builtin_node_types_are_registered(self):
        from workflow.registry import get_node_factory

        for node_type in ("data-source", "filter", "gate", "notify", "order"):
            assert get_node_factory(node_type) is not None

    def test_unknown_node_type_raises(self):
        from workflow.registry import UnknownNodeError, get_node_factory

        with pytest.raises(UnknownNodeError):
            get_node_factory("does-not-exist")


# ── executor ────────────────────────────────────────────────────────

def _graph(nodes, edges):
    return {"nodes": nodes, "edges": edges}


class TestExecutorOrdering:
    def test_data_source_filter_notify_execute_in_order(self, monkeypatch):
        import workflow.nodes as nodes_mod
        from workflow.executor import execute_graph

        # data-source wraps an existing fetcher seam — mock it, never hit UW.
        monkeypatch.setattr(
            nodes_mod,
            "run_data_source",
            lambda source, params: [
                {"ticker": "AAA", "score": 80},
                {"ticker": "BBB", "score": 10},
            ],
        )

        dispatched = []
        monkeypatch.setattr(
            nodes_mod,
            "dispatch_notification",
            lambda rows, channel: dispatched.append((channel, list(rows))),
        )

        graph = _graph(
            nodes=[
                {"id": "n1", "type": "data-source", "params": {"source": "scanner"}},
                {"id": "n2", "type": "filter", "params": {"expression": "score >= 60"}},
                {"id": "n3", "type": "notify", "params": {"channel": "service_health"}},
            ],
            edges=[{"from": "n1", "to": "n2"}, {"from": "n2", "to": "n3"}],
        )

        report = execute_graph(graph)

        assert [step.node_id for step in report.steps] == ["n1", "n2", "n3"]
        # filter kept only the high-score row
        assert dispatched == [("service_health", [{"ticker": "AAA", "score": 80}])]
        assert report.ok is True

    def test_cycle_is_rejected(self):
        from workflow.executor import WorkflowError, execute_graph

        graph = _graph(
            nodes=[
                {"id": "n1", "type": "filter", "params": {"expression": "True"}},
                {"id": "n2", "type": "filter", "params": {"expression": "True"}},
            ],
            edges=[{"from": "n1", "to": "n2"}, {"from": "n2", "to": "n1"}],
        )
        with pytest.raises(WorkflowError):
            execute_graph(graph)


class TestGateNode:
    def test_gate_blocks_graph_when_convexity_fails(self, monkeypatch):
        import workflow.nodes as nodes_mod
        from workflow.executor import execute_graph

        monkeypatch.setattr(
            nodes_mod,
            "run_data_source",
            lambda source, params: [{"ticker": "AAA", "max_gain": 100, "max_loss": 100}],
        )
        fired = []
        monkeypatch.setattr(
            nodes_mod,
            "dispatch_notification",
            lambda rows, channel: fired.append(channel),
        )

        graph = _graph(
            nodes=[
                {"id": "n1", "type": "data-source", "params": {"source": "scanner"}},
                {
                    "id": "n2",
                    "type": "gate",
                    "params": {"gate": "convexity", "max_gain": "max_gain", "max_loss": "max_loss"},
                },
                {"id": "n3", "type": "notify", "params": {"channel": "service_health"}},
            ],
            edges=[{"from": "n1", "to": "n2"}, {"from": "n2", "to": "n3"}],
        )

        report = execute_graph(graph)

        assert report.ok is False
        assert report.blocked_by == "n2"
        assert report.blocked_gate == "convexity"
        # the downstream notify must NOT have fired
        assert fired == []

    def test_gate_passes_through_when_kelly_has_edge(self, monkeypatch):
        import workflow.nodes as nodes_mod
        from workflow.executor import execute_graph

        monkeypatch.setattr(
            nodes_mod,
            "run_data_source",
            lambda source, params: [{"ticker": "AAA", "prob_win": 0.7, "odds": 2.0}],
        )
        fired = []
        monkeypatch.setattr(
            nodes_mod,
            "dispatch_notification",
            lambda rows, channel: fired.append((channel, list(rows))),
        )

        graph = _graph(
            nodes=[
                {"id": "n1", "type": "data-source", "params": {"source": "scanner"}},
                {
                    "id": "n2",
                    "type": "gate",
                    "params": {"gate": "kelly", "prob_win": "prob_win", "odds": "odds"},
                },
                {"id": "n3", "type": "notify", "params": {"channel": "service_health"}},
            ],
            edges=[{"from": "n1", "to": "n2"}, {"from": "n2", "to": "n3"}],
        )

        report = execute_graph(graph)
        assert report.ok is True
        assert fired and fired[0][0] == "service_health"


class TestOrderNode:
    def test_order_node_requires_confirmation(self, monkeypatch):
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
            "emit_order",
            lambda rows, params: placed.append(list(rows)),
        )

        graph = _graph(
            nodes=[
                {"id": "n1", "type": "data-source", "params": {"source": "scanner"}},
                {"id": "n2", "type": "order", "params": {"structure": "long_call"}},
            ],
            edges=[{"from": "n1", "to": "n2"}],
        )

        # No confirmation token → order must NOT fire, graph reports blocked.
        report = execute_graph(graph)
        assert report.ok is False
        assert report.blocked_by == "n2"
        assert report.requires_confirmation is True
        assert placed == []

    def test_order_node_fires_with_confirmation(self, monkeypatch):
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
            "emit_order",
            lambda rows, params: placed.append(list(rows)),
        )

        graph = _graph(
            nodes=[
                {"id": "n1", "type": "data-source", "params": {"source": "scanner"}},
                {"id": "n2", "type": "order", "params": {"structure": "long_call"}},
            ],
            edges=[{"from": "n1", "to": "n2"}],
        )

        report = execute_graph(graph, confirm_order=True)
        assert report.ok is True
        assert placed == [[{"ticker": "AAA", "score": 80}]]
