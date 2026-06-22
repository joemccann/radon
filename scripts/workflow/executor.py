"""Graph executor.

A graph is ``{"nodes": [...], "edges": [...]}``. Nodes carry an ``id``, a
``type`` (resolved through the registry), and a ``params`` dict. Edges are
``{"from": id, "to": id}``. The executor topologically sorts the DAG, threads
the row-set from each node into its successor, and stops at the first node that
blocks (a failing gate or an unconfirmed order node).

This first increment supports a LINEAR pipeline (each node has at most one
inbound edge). Fan-in / fan-out merge semantics are scaffolding — the executor
detects a cycle and rejects it, and it executes a branch in topological order,
but it does not yet merge multiple upstream row-sets. That limitation is called
out in the manifest, not hidden.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .nodes import NodeOutcome
from .registry import get_node_factory


class WorkflowError(ValueError):
    """Raised for a structurally invalid graph (cycle, dangling edge, dup id)."""


@dataclass
class ExecutionStep:
    node_id: str
    node_type: str
    rows_in: int
    rows_out: int
    blocked: bool
    info: dict = field(default_factory=dict)


@dataclass
class GraphReport:
    ok: bool
    steps: list = field(default_factory=list)
    blocked_by: Optional[str] = None
    blocked_gate: Optional[str] = None
    requires_confirmation: bool = False
    final_rows: list = field(default_factory=list)


def execute_graph(graph: dict, *, confirm_order: bool = False) -> GraphReport:
    """Execute ``graph`` and return a ``GraphReport``.

    ``confirm_order`` is the OrderRiskGate confirmation token: order-emitting
    nodes block unless it is ``True``.
    """
    node_index = _index_nodes(graph.get("nodes", []))
    order = _topological_order(node_index, graph.get("edges", []))
    predecessors = _predecessors(graph.get("edges", []))

    run_ctx = {"confirm_order": confirm_order}
    outputs: dict[str, list] = {}
    report = GraphReport(ok=True)

    for node_id in order:
        node = node_index[node_id]
        rows_in = _gather_inputs(node_id, predecessors, outputs)
        outcome = _run_node(node, rows_in, run_ctx)
        outputs[node_id] = outcome.rows
        report.steps.append(
            ExecutionStep(
                node_id=node_id,
                node_type=node["type"],
                rows_in=len(rows_in),
                rows_out=len(outcome.rows),
                blocked=outcome.blocked,
                info=outcome.info,
            )
        )
        if outcome.blocked:
            report.ok = False
            report.blocked_by = node_id
            report.blocked_gate = outcome.gate
            report.requires_confirmation = outcome.requires_confirmation
            return report
        report.final_rows = outcome.rows

    return report


# ── helpers ─────────────────────────────────────────────────────────

def _index_nodes(raw_nodes: list) -> dict:
    index: dict[str, dict] = {}
    for node in raw_nodes:
        node_id = node.get("id")
        if not node_id:
            raise WorkflowError("every node needs an 'id'")
        if node_id in index:
            raise WorkflowError(f"duplicate node id: {node_id!r}")
        index[node_id] = node
    return index


def _predecessors(edges: list) -> dict:
    preds: dict[str, list] = {}
    for edge in edges:
        preds.setdefault(edge["to"], []).append(edge["from"])
    return preds


def _gather_inputs(node_id: str, predecessors: dict, outputs: dict) -> list:
    """Concatenate the row-sets of this node's predecessors. A source node with
    no predecessors starts from an empty row-set (it produces its own rows)."""
    rows: list = []
    for pred in predecessors.get(node_id, []):
        rows.extend(outputs.get(pred, []))
    return rows


def _run_node(node: dict, rows_in: list, run_ctx: dict) -> NodeOutcome:
    factory = get_node_factory(node["type"])
    node_callable = factory()
    return node_callable(rows_in, node.get("params", {}), run_ctx)


def _topological_order(node_index: dict, edges: list) -> list:
    """Kahn's algorithm. Raises ``WorkflowError`` on a dangling edge or a
    cycle. Deterministic: ties broken by insertion order of the node list."""
    insertion = list(node_index.keys())
    indegree = {node_id: 0 for node_id in insertion}
    adjacency: dict[str, list] = {node_id: [] for node_id in insertion}

    for edge in edges:
        src, dst = edge.get("from"), edge.get("to")
        if src not in node_index or dst not in node_index:
            raise WorkflowError(f"edge references unknown node: {edge}")
        adjacency[src].append(dst)
        indegree[dst] += 1

    ready = [node_id for node_id in insertion if indegree[node_id] == 0]
    ordered: list[str] = []
    while ready:
        node_id = ready.pop(0)
        ordered.append(node_id)
        for successor in adjacency[node_id]:
            indegree[successor] -= 1
            if indegree[successor] == 0:
                ready.append(successor)

    if len(ordered) != len(insertion):
        raise WorkflowError("graph contains a cycle")
    return ordered
