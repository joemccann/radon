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

import json
import re
import time
from dataclasses import dataclass, field
from typing import Optional

from .nodes import NodeOutcome
from .registry import get_node_factory


class WorkflowError(ValueError):
    """Raised for a structurally invalid graph (cycle, dangling edge, dup id)."""


MAX_GRAPH_BYTES = 65_536
MAX_NODES = 32
MAX_EDGES = 64
MAX_DEPTH = 16
MAX_EXTERNAL_NODES = 8
MAX_PARAMS = 16
MAX_PARAM_TEXT = 1_024
MAX_ROWS = 1_000
MAX_RESULT_BYTES = 1_048_576
MAX_EXECUTION_SECONDS = 30.0
_NODE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_NODE_TYPE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
_EXTERNAL_TYPES = frozenset({"data-source", "notify", "order"})


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


def execute_graph(
    graph: dict,
    *,
    confirm_order: bool = False,
    max_duration_s: float = MAX_EXECUTION_SECONDS,
) -> GraphReport:
    """Execute ``graph`` and return a ``GraphReport``.

    ``confirm_order`` is the OrderRiskGate confirmation token: order-emitting
    nodes block unless it is ``True``.
    """
    _validate_graph(graph)
    node_index = _index_nodes(graph["nodes"])
    order = _topological_order(node_index, graph["edges"])
    predecessors = _predecessors(graph["edges"])
    deadline = time.monotonic() + max(0.1, min(float(max_duration_s), MAX_EXECUTION_SECONDS))

    run_ctx = {"confirm_order": confirm_order}
    outputs: dict[str, list] = {}
    report = GraphReport(ok=True)

    for node_id in order:
        if time.monotonic() > deadline:
            raise WorkflowError("workflow execution deadline exceeded")
        node = node_index[node_id]
        rows_in = _gather_inputs(node_id, predecessors, outputs)
        _validate_rows(rows_in, "input")
        outcome = _run_node(node, rows_in, run_ctx)
        _validate_rows(outcome.rows, f"node {node_id} output")
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

def _bounded_scalar(value) -> bool:
    return (
        value is None
        or isinstance(value, bool)
        or (isinstance(value, (int, float)) and not isinstance(value, bool))
        or (isinstance(value, str) and len(value) <= MAX_PARAM_TEXT)
    )


def _validate_graph(graph: dict) -> None:
    if not isinstance(graph, dict):
        raise WorkflowError("graph must be an object")
    try:
        encoded = json.dumps(graph, separators=(",", ":"), allow_nan=False).encode()
    except (TypeError, ValueError) as exc:
        raise WorkflowError("graph must be finite JSON") from exc
    if len(encoded) > MAX_GRAPH_BYTES:
        raise WorkflowError(f"graph exceeds {MAX_GRAPH_BYTES} bytes")

    nodes = graph.get("nodes")
    edges = graph.get("edges")
    if not isinstance(nodes, list) or not isinstance(edges, list):
        raise WorkflowError("graph must have nodes[] and edges[]")
    if len(nodes) > MAX_NODES:
        raise WorkflowError(f"graph exceeds {MAX_NODES} nodes")
    if len(edges) > MAX_EDGES:
        raise WorkflowError(f"graph exceeds {MAX_EDGES} edges")

    external_nodes = 0
    for node in nodes:
        if not isinstance(node, dict):
            raise WorkflowError("every node must be an object")
        node_id = node.get("id")
        node_type = node.get("type")
        params = node.get("params")
        if not isinstance(node_id, str) or not _NODE_ID.fullmatch(node_id):
            raise WorkflowError("every node needs a valid id")
        if not isinstance(node_type, str) or not _NODE_TYPE.fullmatch(node_type):
            raise WorkflowError(f"node {node_id} has an invalid type")
        if node_type == "order":
            raise WorkflowError(
                "workflow order execution is disabled until a complete "
                "risk-reviewed contract editor is available"
            )
        if node_type in _EXTERNAL_TYPES:
            external_nodes += 1
        if not isinstance(params, dict) or len(params) > MAX_PARAMS:
            raise WorkflowError(f"node {node_id} params exceed bounds")
        if any(
            not isinstance(key, str)
            or not _NODE_ID.fullmatch(key)
            or not _bounded_scalar(value)
            for key, value in params.items()
        ):
            raise WorkflowError(f"node {node_id} params exceed scalar bounds")
    if external_nodes > MAX_EXTERNAL_NODES:
        raise WorkflowError(f"graph exceeds {MAX_EXTERNAL_NODES} external nodes")

    node_ids = {node["id"] for node in nodes}
    if len(node_ids) != len(nodes):
        raise WorkflowError("graph contains duplicate node ids")
    indegree = {node_id: 0 for node_id in node_ids}
    adjacency = {node_id: [] for node_id in node_ids}
    for edge in edges:
        if not isinstance(edge, dict):
            raise WorkflowError("every edge must be an object")
        source, target = edge.get("from"), edge.get("to")
        if source not in node_ids or target not in node_ids:
            raise WorkflowError("edge references unknown node")
        adjacency[source].append(target)
        indegree[target] += 1

    depth = {node_id: 1 for node_id, value in indegree.items() if value == 0}
    ready = list(depth)
    visited = 0
    while ready:
        node_id = ready.pop(0)
        visited += 1
        for successor in adjacency[node_id]:
            depth[successor] = max(depth.get(successor, 1), depth[node_id] + 1)
            indegree[successor] -= 1
            if indegree[successor] == 0:
                ready.append(successor)
    if visited != len(node_ids):
        raise WorkflowError("graph contains a cycle")
    if depth and max(depth.values()) > MAX_DEPTH:
        raise WorkflowError(f"graph exceeds dependency depth {MAX_DEPTH}")


def _validate_rows(rows, label: str) -> None:
    if not isinstance(rows, list):
        raise WorkflowError(f"{label} must be a row list")
    if len(rows) > MAX_ROWS:
        raise WorkflowError(f"{label} exceeds {MAX_ROWS} rows")
    try:
        size = len(json.dumps(rows, separators=(",", ":"), allow_nan=False).encode())
    except (TypeError, ValueError) as exc:
        raise WorkflowError(f"{label} must be finite JSON") from exc
    if size > MAX_RESULT_BYTES:
        raise WorkflowError(f"{label} exceeds {MAX_RESULT_BYTES} bytes")

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
