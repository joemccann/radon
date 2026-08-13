import pytest


def test_order_graph_is_rejected_before_any_node_executes(monkeypatch):
    from workflow import nodes
    from workflow.executor import WorkflowError, execute_graph

    monkeypatch.setattr(
        nodes,
        "run_data_source",
        lambda *_: (_ for _ in ()).throw(AssertionError("source must not run")),
    )
    graph = {
        "nodes": [
            {"id": "source", "type": "data-source", "params": {"source": "scanner"}},
            {"id": "order", "type": "order", "params": {"structure": "long_call"}},
        ],
        "edges": [{"from": "source", "to": "order"}],
    }

    with pytest.raises(WorkflowError, match="order execution is disabled"):
        execute_graph(graph, confirm_order=True)
