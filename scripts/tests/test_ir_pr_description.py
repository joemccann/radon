"""IR PR bodies must carry the six sections; placeholders never ship."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import ir_pr_description as desc  # noqa: E402

VALID_BODY = """fix: keep the page responder green on a ledger timeout

## What broke
Symptom: radon-grok-page-responder.service exited Result=exit-code and
paged P1 about itself. Page a1c550c1f43ba50af5bd701cb697acd4 first seen
2026-09-26T17:15:04Z. Error excerpt: HranaHttpError TimeoutError on complete_page.
## Root cause
run_cycle treated a hrana timeout as fatal on ledger reads, so the oneshot
exited 1 and systemd marked it failed.
## What changed
- scripts/grok_page_responder.py: catch ledger read timeouts and exit 0.
- scripts/tests/test_grok_page_ledger_timeout.py: four regression tests.
## How it was verified
Focused pytest for the new timeout tests passed locally. Not verified on the VPS.
## Risk and rollback
The TimeoutError matcher is broad. Rollback: revert this commit.
## Still open
Why Grok waited about 24h after the page is unverified.
"""

BAD_CURRENT = "grok incident fix on fix/grok-page-ledger-timeout"


class TestValidator:
    def test_valid_body_returns_every_section(self):
        sections = desc.validate_ir_description(VALID_BODY)
        assert set(sections) == set(desc.REQUIRED_SECTIONS)
        assert "TimeoutError" in sections["What broke"]
        assert "run_cycle" in sections["Root cause"]

    def test_empty_body_fails(self):
        with pytest.raises(desc.IrDescriptionError, match="empty"):
            desc.validate_ir_description("")

    def test_placeholder_current_bad_body_is_rejected(self):
        with pytest.raises(desc.IrDescriptionError, match="placeholder|missing"):
            desc.validate_ir_description(
                BAD_CURRENT, branch="fix/grok-page-ledger-timeout"
            )

    def test_todo_section_fails(self):
        body = VALID_BODY.replace(
            "Why Grok waited about 24h after the page is unverified.",
            "TODO",
        )
        with pytest.raises(desc.IrDescriptionError, match="placeholder"):
            desc.validate_ir_description(body, branch="fix/x")

    def test_branch_name_only_section_fails(self):
        body = VALID_BODY.replace(
            "The TimeoutError matcher is broad. Rollback: revert this commit.",
            "fix/relay-restart",
        )
        with pytest.raises(desc.IrDescriptionError, match="placeholder"):
            desc.validate_ir_description(body, branch="fix/relay-restart")

    def test_missing_section_fails(self):
        body = "\n".join(
            line
            for line in VALID_BODY.splitlines()
            if "Still open" not in line and "24h" not in line
        )
        with pytest.raises(desc.IrDescriptionError, match="Still open"):
            desc.validate_ir_description(body)


class TestCompose:
    def test_title_and_body_include_page_and_ci(self):
        title, body = desc.description_from_commit(
            VALID_BODY,
            branch="fix/grok-page-ledger-timeout",
            page={
                "page_id": "a1c550c1f43ba50af5bd701cb697acd4",
                "severity": "P1",
                "paged_at": "2026-09-27T00:15:04Z",
                "result": "code_fix: ledger timeout no longer fails the oneshot",
            },
            ci_urls=["https://github.com/joemccann/radon/actions/runs/36361801938"],
        )
        assert "exited Result=exit-code" in title or "paged P1" in title
        for heading in desc.REQUIRED_SECTIONS:
            assert f"## {heading}" in body
        assert "a1c550c1f43ba50af5bd701cb697acd4" in body
        assert "P1" in body
        assert "36361801938" in body
        assert "Ledger result" in body

    def test_extracts_page_id(self):
        assert (
            desc.extract_page_id(VALID_BODY)
            == "a1c550c1f43ba50af5bd701cb697acd4"
        )
