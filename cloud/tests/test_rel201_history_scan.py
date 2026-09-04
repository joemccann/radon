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


# ── T-415: parse the workflow, do not substring it ────────────────────
#
# Every assertion above is a substring over the raw file: `"issue" in src`
# matches `issues: write`, a comment or a step name, and `"gitleaks detect"
# in src` says nothing about which config or args the command actually
# runs. Parse the YAML and read the argv.


def _workflow() -> dict:
    import yaml

    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _triggers(workflow: dict) -> dict:
    # YAML 1.1 resolves the bare key `on` to the boolean True.
    return workflow.get("on", workflow.get(True))


def _steps(workflow: dict) -> list[dict]:
    jobs = workflow["jobs"]
    assert len(jobs) == 1, sorted(jobs)
    return next(iter(jobs.values()))["steps"]


def _argv(step: dict) -> list[str]:
    import shlex

    return shlex.split(step.get("run", ""))


class TestWorkflowIsParsed:
    def test_it_is_scheduled_with_a_cron(self):
        schedule = _triggers(_workflow())["schedule"]
        assert schedule and schedule[0]["cron"].strip()

    def test_the_detect_step_passes_the_repo_config_as_a_parsed_token(self):
        steps = _steps(_workflow())
        def _is_detect(step: dict) -> bool:
            argv = _argv(step)
            return any(
                argv[i] == "gitleaks" and argv[i + 1] == "detect"
                for i in range(len(argv) - 1)
            )

        detect = [step for step in steps if _is_detect(step)]
        assert len(detect) == 1, [step.get("name") for step in detect]
        argv = _argv(detect[0])
        assert "--log-opts" not in argv
        idx = argv.index("--config")
        assert argv[idx + 1] == "cloud/.gitleaks.toml", argv

    def test_the_failure_step_actually_opens_an_issue(self):
        steps = _steps(_workflow())
        failure = [step for step in steps if str(step.get("if", "")).strip() == "failure()"]
        assert len(failure) == 1, [step.get("name") for step in steps]
        argv = _argv(failure[0])
        for token in ("gh", "issue", "create"):
            assert token in argv, argv
