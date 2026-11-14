"""Unattended runner prompts take their checkpoint from a runner-filtered file.

Anyone can comment on a public issue. A prompt sentence telling the model to
ignore untrusted authors is a probabilistic control over attacker-formatted
text in the task position, so no prompt may read rolling-issue comments
directly. Each loop's pre-run hook writes `scratch/audit-context.md` from
owner, member and collaborator comments only (scripts/nightly_audit_context.py)
and the prompt reads that file.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
PROMPTS = REPO / ".claude" / "runner-prompts"
LOOPS = REPO / "scripts" / "runner" / "loops"
HOOKS = REPO / "scripts" / "runner" / "hooks"
CONTEXT_FILE = "scratch/audit-context.md"
ROLLING_ISSUE = re.compile(r"gh issue list --label (?P<label>[a-z-]+) --state open")


def _loop_env(path: Path) -> dict[str, str]:
    env = {}
    for line in path.read_text().splitlines():
        m = re.match(r'^([A-Z_]+)=("?)(.*)\2$', line.strip())
        if m:
            env[m.group(1)] = m.group(3)
    return env


def _issue_readers() -> list[Path]:
    return sorted(p for p in PROMPTS.glob("*.md") if ROLLING_ISSUE.search(p.read_text()))


def _loop_for_label(label: str) -> tuple[str, dict[str, str]]:
    for env_path in LOOPS.glob("*.env"):
        env = _loop_env(env_path)
        if env.get("DEADMAN_LABEL") == label:
            return env_path.stem, env
    raise AssertionError(f"no loop env carries DEADMAN_LABEL={label}")


def test_some_prompt_reads_a_rolling_issue():
    assert _issue_readers()


@pytest.mark.parametrize("prompt", _issue_readers(), ids=lambda p: p.name)
def test_no_prompt_reads_issue_comments_directly(prompt: Path):
    text = prompt.read_text()
    assert "--comments" not in text, prompt.name
    assert "gh issue view" not in text, prompt.name
    assert not re.search(r"issues/[^ ]*/comments", text), prompt.name


@pytest.mark.parametrize("prompt", _issue_readers(), ids=lambda p: p.name)
def test_the_checkpoint_comes_from_the_runner_filtered_file(prompt: Path):
    text = prompt.read_text()
    assert CONTEXT_FILE in text, prompt.name
    label = ROLLING_ISSUE.search(text).group("label")
    loop, env = _loop_for_label(label)
    pre_run = env.get("PRE_RUN", "")
    assert pre_run, f"{loop} has no PRE_RUN hook to write {CONTEXT_FILE}"
    hook = (REPO / "scripts" / "runner" / pre_run).read_text()
    assert "nightly_audit_context.py" in hook, f"{pre_run} does not write the checkpoint"
    assert "--label" in hook, f"{pre_run} does not filter by the rolling-issue label"


def test_the_filter_is_code_not_prose():
    helper = (REPO / "scripts" / "nightly_audit_context.py").read_text()
    assert 'frozenset({"OWNER", "MEMBER", "COLLABORATOR"})' in helper


PR_LIST = re.compile(r"`gh pr list[^`]*`")


@pytest.mark.parametrize("prompt", _issue_readers(), ids=lambda p: p.name)
def test_open_pr_lookups_exclude_fork_pull_requests(prompt: Path):
    """A fork PR's branch name, title and body are public input. Every open-PR
    lookup a prompt specifies must fetch isCrossRepository so it can be dropped."""
    text = prompt.read_text()
    commands = PR_LIST.findall(text)
    assert commands, prompt.name
    for command in commands:
        assert "isCrossRepository" in command, (prompt.name, command)
