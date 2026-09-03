"""REL-201 (R-562): secret-scan history is re-audited on a schedule — a rule
or version tightening otherwise never re-checks what already slipped past."""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
WORKFLOW = REPO / ".github" / "workflows" / "gitleaks-history.yml"


class TestScheduledFullHistoryScan:
    def test_the_workflow_exists_with_a_schedule(self):
        assert WORKFLOW.is_file(), "no scheduled full-history gitleaks workflow"
        src = WORKFLOW.read_text()
        assert "schedule:" in src and "cron:" in src

    def test_it_scans_full_history(self):
        # Strip comments first: a comment naming the flag would satisfy or
        # break the assertion (the 2026-08-26 lesson, again).
        src = "\n".join(
            line for line in WORKFLOW.read_text().splitlines()
            if not line.lstrip().startswith("#")
        )
        assert "fetch-depth: 0" in src
        assert "gitleaks detect" in src
        assert "cloud/.gitleaks.toml" in src
        # Full history means NO per-event --log-opts range.
        assert "--log-opts" not in src

    def test_failure_pages_rather_than_blocks(self):
        """A scheduled run has no PR to block; its failure must surface as
        an operator-visible signal (an issue the runner forensics read)."""
        src = WORKFLOW.read_text()
        assert "if: failure()" in src
        assert "issue" in src

    def test_the_binary_is_checksum_pinned(self):
        src = WORKFLOW.read_text()
        assert "sha256sum --check" in src
