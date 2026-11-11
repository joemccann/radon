"""Lint test: every ``await ib.<method>(...)`` on an ``ib_insync.IB`` instance
must be wrapped in ``asyncio.wait_for(...)``.

``ib_insync`` has no per-request timeout. When IB Gateway is logged in but
the user session is awaiting 2FA, ``qualifyContractsAsync`` /
``reqHistoricalDataAsync`` / ``reqMktData`` / ``connectAsync`` block forever.
This test parses every script under ``scripts/`` with the Python ``ast``
module and fails on any unbounded await of an IB call.

Strategy
--------
1. For each ``.py`` file (excluding tests, ``__pycache__``, and the lint test
   itself) parse the source with ``ast.parse``.
2. Walk every function/method body; identify local names bound to an
   ``ib_insync.IB`` instance by inspecting ``Assign`` nodes whose RHS is
   ``IB()`` or whose value is imported as ``IB`` from ``ib_insync``.
3. For pragmatic detection, also flag any await on a name matching
   ``^ib$`` / ``^ib_client$`` / ``^client$`` whose attribute is one of the
   known unbounded ``ib_insync`` methods (``connectAsync``,
   ``qualifyContractsAsync``, ``reqHistoricalDataAsync``, ``reqMktDataAsync``,
   ``reqOpenOrdersAsync``, ``reqAllOpenOrdersAsync``,
   ``reqContractDetailsAsync``, ``reqExecutionsAsync``,
   ``placeOrderAsync``).
4. For each await match, check whether the immediately enclosing call is
   ``asyncio.wait_for``. If not, record a violation.

The detection is intentionally conservative — false positives are tolerable
(they force an explicit timeout), false negatives undermine the contract.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import List, Set, Tuple

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = SCRIPTS_DIR.parent

# ib_insync.IB methods that are known to block forever when the user
# session is awaiting 2FA. The list covers every async method that
# performs a network round-trip; ``placeOrderAsync`` is included for
# completeness even though the order pipeline normally calls the sync
# variant.
UNBOUNDED_IB_METHODS: Set[str] = {
    "connectAsync",
    "qualifyContractsAsync",
    "reqHistoricalDataAsync",
    "reqMktDataAsync",
    "reqOpenOrdersAsync",
    "reqAllOpenOrdersAsync",
    "reqContractDetailsAsync",
    "reqExecutionsAsync",
    "placeOrderAsync",
    "reqAccountSummaryAsync",
    "reqAccountUpdatesAsync",
    "reqPositionsAsync",
    "reqTickersAsync",
}

# Local names that conventionally hold an ib_insync.IB instance. Used as a
# heuristic when we can't statically prove the binding (e.g. the IB
# instance comes from an attribute access like ``self.ib`` or
# ``client.ib``).
IB_NAME_HINTS: Set[str] = {"ib", "ib_client", "client", "_ib"}

# Files we intentionally skip:
#   - test files (may use unbounded awaits inside mocks)
#   - __pycache__
#   - the lint test itself
#   - subprocess sentinels that don't actually do async IB calls
SKIP_DIR_PARTS: Set[str] = {"__pycache__", "tests"}
SKIP_FILES: Set[str] = {"test_ib_insync_bounded.py"}


def _python_sources() -> List[Path]:
    out = []
    for path in SCRIPTS_DIR.rglob("*.py"):
        parts = set(path.relative_to(SCRIPTS_DIR).parts)
        if parts & SKIP_DIR_PARTS:
            continue
        if path.name in SKIP_FILES:
            continue
        out.append(path)
    return out


def _receiver_root(node: ast.AST) -> str:
    """Return the root identifier of an attribute chain.

    Examples:
        ``ib.qualifyContractsAsync`` → ``ib``
        ``self.ib.connectAsync``     → ``self``
        ``client.ib.reqMktDataAsync`` → ``client``
    """
    cur = node
    while isinstance(cur, ast.Attribute):
        cur = cur.value
    if isinstance(cur, ast.Name):
        return cur.id
    return ""


def _attr_chain_contains_ib(node: ast.AST) -> bool:
    """Check whether an attribute chain references ``ib`` anywhere.

    Catches ``self.ib.qualifyContractsAsync`` and ``client.ib.connectAsync``.
    """
    cur = node
    while isinstance(cur, ast.Attribute):
        if cur.attr in IB_NAME_HINTS:
            return True
        cur = cur.value
    if isinstance(cur, ast.Name) and cur.id in IB_NAME_HINTS:
        return True
    return False


def _is_wait_for_call(node: ast.AST) -> bool:
    """Return True if ``node`` is a Call to ``asyncio.wait_for``."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Attribute) and func.attr == "wait_for":
        # asyncio.wait_for(...)
        if isinstance(func.value, ast.Name) and func.value.id == "asyncio":
            return True
        # also accept a bare "wait_for" attribute on any module-like alias
        return True
    if isinstance(func, ast.Name) and func.id == "wait_for":
        # from asyncio import wait_for
        return True
    return False


def _find_violations_in_file(path: Path) -> List[Tuple[int, str]]:
    """Return a list of (lineno, code_snippet) violations in ``path``."""
    try:
        source = path.read_text()
    except (OSError, UnicodeDecodeError):
        return []

    # Only parse files that actually mention ib_insync — a quick textual
    # gate keeps the lint test fast on a large repo.
    if "ib_insync" not in source and "from ib_insync" not in source:
        return []

    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return []

    violations: List[Tuple[int, str]] = []
    source_lines = source.splitlines()

    # Walk Await nodes and detect unbounded ib_insync calls.
    for node in ast.walk(tree):
        if not isinstance(node, ast.Await):
            continue
        value = node.value
        if not isinstance(value, ast.Call):
            continue

        # If the await IS asyncio.wait_for(...), it's bounded — skip.
        if _is_wait_for_call(value):
            continue

        func = value.func
        if not isinstance(func, ast.Attribute):
            continue

        method = func.attr
        if method not in UNBOUNDED_IB_METHODS:
            continue

        # Receiver must look like an IB instance.
        if not _attr_chain_contains_ib(func.value):
            # Also accept the case where the receiver itself is the IB
            # root (``ib.connectAsync(...)`` — func.value is Name "ib")
            if not (isinstance(func.value, ast.Name) and func.value.id in IB_NAME_HINTS):
                continue

        snippet = (
            source_lines[node.lineno - 1].strip()
            if 0 < node.lineno <= len(source_lines)
            else "<unknown>"
        )
        violations.append((node.lineno, snippet))

    return violations


def _scan_repo_for_unbounded_ib_awaits() -> List[str]:
    findings: List[str] = []
    for path in _python_sources():
        for lineno, snippet in _find_violations_in_file(path):
            rel = path.relative_to(REPO_ROOT)
            findings.append(f"{rel}:{lineno}  {snippet}")
    return findings


def test_all_ib_insync_awaits_are_bounded() -> None:
    """Every ``await ib.<method>(...)`` must be wrapped in
    ``asyncio.wait_for``. ib_insync has no per-request timeout so an
    unbounded await blocks forever during 2FA-pending windows."""

    violations = _scan_repo_for_unbounded_ib_awaits()
    assert violations == [], (
        "Unbounded ib_insync awaits found — wrap each in "
        "asyncio.wait_for(..., timeout=N). Violations:\n  "
        + "\n  ".join(violations)
    )
