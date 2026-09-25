"""The native security-audit workflow must never pipe raw git history into a model.

2026-09-25 security-nightly remediate (OPERATOR_REQUIRED): the shared preamble
told every finder to run `git log --all -p | grep for leaked secrets`, and the
secrets dimension told its finder to grep the full history. Either one streams
live secret VALUES (the repo is public and history still holds creds pending
rotation) straight into model context and any transcript. The finders must read
redacted gitleaks metadata only: rule, file, line, commit, never a value.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
WORKFLOWS = sorted((REPO / ".claude" / "workflows").glob("security-audit*.mjs"))

RAW_HISTORY = re.compile(r"log\s+--all\s+-p|log\s+-p\s+--all|-p\s+-S\s|grep the full history|git\s+log[^`'\n]*\|\s*grep")


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_no_workflow_streams_raw_history(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    hits = [m.group(0) for m in RAW_HISTORY.finditer(text)]
    assert not hits, f"{path.name} still sends raw git history to a model: {hits}"


def test_secrets_dimension_reads_redacted_gitleaks_metadata_only() -> None:
    text = (REPO / ".claude" / "workflows" / "security-audit.mjs").read_text(encoding="utf-8")
    start = text.index("key: 'secrets'")
    scope = text[start : text.index("},", start)]
    assert "gitleaks" in scope
    assert "--redact" in scope
    assert "--report-format json" in scope
    assert "cloud/.gitleaks.toml" in scope
    for field in ("RuleID", "File", "StartLine", "Commit"):
        assert field in scope, field
    assert "Secret" in scope and "Match" in scope, "must name the value fields it may not read"


def test_workflows_found() -> None:
    assert any(p.name == "security-audit.mjs" for p in WORKFLOWS)
