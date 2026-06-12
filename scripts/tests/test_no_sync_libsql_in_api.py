"""AST lint: the FastAPI process (``scripts/api/``) must never touch sync libsql.

``libsql_experimental`` holds the GIL while a native ``execute()``/``commit()``
blocks, so even ``asyncio.to_thread`` cannot bound it — a hung Turso call
starves the single uvicorn event loop from ANY thread. This wedged the API
twice on 2026-06-11 (commits c9e518a + 2647c93,
``feedback_no_sync_libsql_on_fastapi_event_loop``). Bounded DB access for the
API process goes through ``scripts/api/db_http.py`` (libSQL HTTP pipeline,
real socket timeout) or a ``run_script`` subprocess.

Two rules, mirroring the ``test_ib_insync_bounded.py`` lint precedent
(fe60b4b — conservative detection, false positives tolerable):

  A. No import of ``db.client`` / ``db.writer`` / ``libsql*`` anywhere under
     ``scripts/api/`` — module level OR function level. Lazy in-function
     imports are exactly how the hazard re-entered before.
  B. No ``get_db()`` call, and no ``.execute()`` / ``.commit()`` on a
     receiver named ``db``, inside any function — belt and braces for code
     that smuggles a connection in without an import in the same file.

Allowed: ``db.service_health_sql`` (pure SQL constants, no libsql import)
and ``api.db_http`` (bounded stdlib hrana).
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import List

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
API_DIR = SCRIPTS_DIR / "api"
REPO_ROOT = SCRIPTS_DIR.parent

# Module names whose import anywhere in scripts/api puts sync libsql in the
# API process. ``db.service_health_sql`` is deliberately NOT here.
BANNED_IMPORT_MODULES = {
    "db.client",
    "db.writer",
    "scripts.db.client",
    "scripts.db.writer",
    "libsql",
    "libsql_client",
    "libsql_experimental",
}

# ``from db import client`` / ``from scripts.db import writer`` shapes.
BANNED_FROM_PACKAGES = {"db": {"client", "writer"}, "scripts.db": {"client", "writer"}}

SKIP_DIR_PARTS = {"__pycache__", "tests"}


def _api_sources() -> List[Path]:
    out = []
    for path in API_DIR.rglob("*.py"):
        if set(path.relative_to(API_DIR).parts) & SKIP_DIR_PARTS:
            continue
        out.append(path)
    return out


def _import_violations(tree: ast.AST) -> List[ast.AST]:
    nodes = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name in BANNED_IMPORT_MODULES for alias in node.names):
                nodes.append(node)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module in BANNED_IMPORT_MODULES:
                nodes.append(node)
            elif module in BANNED_FROM_PACKAGES and any(
                alias.name in BANNED_FROM_PACKAGES[module] for alias in node.names
            ):
                nodes.append(node)
    return nodes


def _receiver_root(node: ast.AST) -> str:
    cur = node
    while isinstance(cur, ast.Attribute):
        cur = cur.value
    return cur.id if isinstance(cur, ast.Name) else ""


def _sync_db_call_violations(tree: ast.AST) -> List[ast.AST]:
    nodes = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == "get_db":
            nodes.append(node)
        elif (
            isinstance(func, ast.Attribute)
            and func.attr in ("execute", "executemany", "commit")
            and _receiver_root(func.value) == "db"
        ):
            nodes.append(node)
    return nodes


def _violations_in_source(source: str, filename: str) -> List[str]:
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError:
        return []
    findings = []
    for node in _import_violations(tree):
        findings.append(f"{filename}:{node.lineno}  banned libsql import")
    for node in _sync_db_call_violations(tree):
        findings.append(f"{filename}:{node.lineno}  sync libsql call (get_db/db.execute/db.commit)")
    return findings


def _scan_api_tree() -> List[str]:
    findings: List[str] = []
    for path in _api_sources():
        rel = str(path.relative_to(REPO_ROOT))
        findings.extend(_violations_in_source(path.read_text(), rel))
    return sorted(findings)


def test_no_sync_libsql_in_api_tree() -> None:
    """scripts/api must contain ZERO sync libsql touch points. Use
    ``api.db_http.hrana_execute`` (bounded HTTP) or a run_script subprocess
    instead. See module docstring for why asyncio.to_thread is NOT a fix."""
    violations = _scan_api_tree()
    assert violations == [], (
        "Sync libsql found in the FastAPI process — this class wedged the API "
        "on 2026-06-11. Route DB access through api/db_http.py (hrana, bounded) "
        "or a subprocess. Violations:\n  " + "\n  ".join(violations)
    )


class TestCheckerCatchesViolations:
    """Prove the lint actually fails on a re-introduction (fixture-based)."""

    def test_flags_module_level_import(self) -> None:
        src = "from db.client import get_db\n"
        assert _violations_in_source(src, "fixture.py") != []

    def test_flags_lazy_in_function_import(self) -> None:
        src = (
            "async def route():\n"
            "    from db.writer import record_service_health\n"
            "    record_service_health('x', 'ok')\n"
        )
        assert _violations_in_source(src, "fixture.py") != []

    def test_flags_from_package_shape(self) -> None:
        src = "from db import client\n"
        assert _violations_in_source(src, "fixture.py") != []

    def test_flags_direct_libsql_import(self) -> None:
        src = "import libsql_experimental as libsql\n"
        assert _violations_in_source(src, "fixture.py") != []

    def test_flags_get_db_and_db_execute_calls(self) -> None:
        src = (
            "async def cash_flows():\n"
            "    db = get_db()\n"
            "    rows = db.execute('SELECT 1').fetchall()\n"
            "    db.commit()\n"
        )
        findings = _violations_in_source(src, "fixture.py")
        assert len(findings) == 3

    def test_clean_source_passes(self) -> None:
        src = (
            "from api import db_http\n"
            "from db.service_health_sql import SERVICE_HEALTH_UPSERT_SQL\n"
            "async def route():\n"
            "    return db_http.hrana_execute('SELECT 1')\n"
        )
        assert _violations_in_source(src, "fixture.py") == []
