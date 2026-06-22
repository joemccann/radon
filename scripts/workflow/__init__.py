"""F14 — Visual flow-pipeline node-graph composer (backend).

A small, fully-tested execution core for operator-authored flow pipelines:

  - ``expression``  — a sandboxed expression evaluator (no imports, no calls,
    no attribute access) used by filter/gate/order nodes.
  - ``gates``       — the SHARED Four-Gates contract. Gate nodes call into this
    one module rather than re-encoding convexity / Kelly anywhere. The Kelly
    branch delegates to the existing ``kelly.kelly`` so there is exactly one
    Kelly implementation in the Python tree.
  - ``registry``    — node-type → factory lookup.
  - ``nodes``       — the built-in node implementations (data-source, filter,
    gate, notify, order). External effects (UW/IB fetch, F6 dispatch, order
    emission) are isolated behind module-level seams so tests stay offline.
  - ``executor``    — topological execution that threads a row-set through the
    graph and stops at the first failing gate or unconfirmed order node.
  - ``graphs``      — Turso persistence (save/load) with a JSON disk fallback.
"""
from .executor import (
    ExecutionStep,
    GraphReport,
    WorkflowError,
    execute_graph,
)
from .expression import ExpressionError, evaluate_expression
from .gates import GateResult, convexity_gate, kelly_gate
from .registry import UnknownNodeError, get_node_factory, register_node

__all__ = [
    "ExecutionStep",
    "GraphReport",
    "WorkflowError",
    "execute_graph",
    "ExpressionError",
    "evaluate_expression",
    "GateResult",
    "convexity_gate",
    "kelly_gate",
    "UnknownNodeError",
    "get_node_factory",
    "register_node",
]
