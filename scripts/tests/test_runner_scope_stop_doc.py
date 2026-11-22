"""docs/runner-scope-and-stop.md cites real runner sources and stays public-safe.

The page is a record of rules that already exist in prompts and scripts.
It must name the Open gaps heading, cite every loop prompt and the stop
path, and must not copy network identifiers out of the loop env files.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DOC = REPO / "docs" / "runner-scope-and-stop.md"
LOOPS = REPO / "scripts" / "runner" / "loops"

REQUIRED = (
    ".claude/runner-prompts/security.md",
    ".claude/runner-prompts/security-deepsec.md",
    ".claude/runner-prompts/testing.md",
    ".claude/runner-prompts/reliability.md",
    ".claude/runner-prompts/documentation.md",
    ".claude/runner-prompts/performance.md",
    ".claude/runner-prompts/ci-performance.md",
    "scripts/runner/run_loop.sh",
    "scripts/runner/install.sh",
    "scripts/runner/hooks/security_pre.sh",
    "scripts/runner/hooks/security_post.sh",
    "scripts/runner/lifecycle_gate.py",
    "scripts/nightly_pr_guard.py",
    "scripts/runner/loops/security.env",
    "scripts/runner/loops/security-deepsec.env",
)

HEADINGS = (
    "## What each loop may touch",
    "## Live-test limits",
    "## When a run must pause or stop",
    "## Commands that stop a run from outside the agent",
    "## End-of-run checklist",
    "## Open gaps",
)


def _text() -> str:
    assert DOC.is_file(), "docs/runner-scope-and-stop.md is missing"
    return DOC.read_text(encoding="utf-8")


def test_page_cites_every_loop_and_the_stop_path():
    text = _text()
    missing = [item for item in REQUIRED if item not in text]
    assert not missing, missing
    for heading in HEADINGS:
        assert heading in text, heading


def test_page_copies_no_network_identifiers():
    text = _text()
    assert "—" not in text and "–" not in text
    assert not re.search(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", text), "IPv4 in the public page"
    assert "tailscale" not in text.lower()
    # Launchd label com.radon.runner contains the letters radon.run but is not the hostname.
    assert not re.search(r"\bradon\.run\b", text.lower())
    for env in sorted(LOOPS.glob("*.env")):
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith("PERF_SSH_TARGET="):
                value = line.split("=", 1)[1].strip()
                assert value and value not in text
