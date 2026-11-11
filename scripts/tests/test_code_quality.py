"""Code quality tests for scripts/ directory.

Verifies:
  - No bare except: clauses exist in any script file
"""

import ast
from pathlib import Path

import pytest


SCRIPTS_DIR = Path(__file__).parent.parent


# ── No bare except: clauses ───────────────────────────────────────

def _collect_python_files():
    """Return all .py files under scripts/, excluding tests and __pycache__."""
    files = []
    for py_file in SCRIPTS_DIR.rglob("*.py"):
        # Skip test files and __pycache__
        if "__pycache__" in str(py_file):
            continue
        files.append(py_file)
    return files


class _BareExceptVisitor(ast.NodeVisitor):
    """AST visitor that detects bare except: clauses."""

    def __init__(self):
        self.bare_excepts: list = []

    def visit_ExceptHandler(self, node: ast.ExceptHandler):
        if node.type is None:
            self.bare_excepts.append(node.lineno)
        self.generic_visit(node)


class TestNoBareExcept:
    """Ensure no bare except: clauses exist in any script file."""

    @pytest.mark.parametrize(
        "py_file",
        _collect_python_files(),
        ids=lambda p: str(p.relative_to(SCRIPTS_DIR)),
    )
    def test_no_bare_except(self, py_file: Path):
        source = py_file.read_text()
        tree = ast.parse(source, filename=str(py_file))
        visitor = _BareExceptVisitor()
        visitor.visit(tree)
        assert visitor.bare_excepts == [], (
            f"Bare except: found in {py_file.name} at line(s) {visitor.bare_excepts}. "
            f"Use 'except Exception:' or a more specific exception type."
        )
