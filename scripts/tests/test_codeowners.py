"""CODEOWNERS requires owner review for CI workflow changes."""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_CODEOWNERS = _ROOT / ".github" / "CODEOWNERS"


def _rules() -> list[tuple[str, list[str]]]:
    rules: list[tuple[str, list[str]]] = []
    for raw in _CODEOWNERS.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        pattern, *owners = line.split()
        rules.append((pattern, owners))
    return rules


def _matches(pattern: str, path: str) -> bool:
    if pattern == "*":
        return True
    body = pattern[1:] if pattern.startswith("/") else pattern
    if body.endswith("/"):
        return path.startswith(body)
    return path == body or path.startswith(body + "/")


def _winner(path: str) -> tuple[str, list[str]]:
    winner: tuple[str, list[str]] | None = None
    for pattern, owners in _rules():
        if _matches(pattern, path):
            winner = (pattern, owners)
    assert winner is not None, path
    return winner


def test_default_owner_stays():
    assert ("*", ["@joemccann"]) in _rules()


def test_ci_workflow_changes_require_owner_review():
    for directory, pattern in (
        (".github/workflows", "/.github/workflows/"),
        (".github/actions", "/.github/actions/"),
    ):
        files = [path for path in (_ROOT / directory).rglob("*") if path.is_file()]
        assert files, directory
        for path in files:
            rel = path.relative_to(_ROOT).as_posix()
            won, owners = _winner(rel)
            assert won == pattern, rel
            assert owners == ["@joemccann"], rel


def test_owner_doc_explains_ci_workflow_review():
    contributing = (_ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    index = (_ROOT / "docs" / "README.md").read_text(encoding="utf-8")
    phrase = "require owner review for CI workflow changes"
    assert phrase in contributing
    assert "/.github/workflows/" in contributing
    assert "/.github/actions/" in contributing
    assert "@joemccann" in contributing
    assert phrase in index
