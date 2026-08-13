from __future__ import annotations

import re
import tomllib
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
ACTIVE_CI = REPO_ROOT / ".github" / "workflows" / "ci.yml"
DEAD_CLOUD_CI = REPO_ROOT / "cloud" / ".github" / "workflows" / "ci.yml"
GITLEAKS_CONFIG = REPO_ROOT / "cloud" / ".gitleaks.toml"

LITERAL_TWS_BASELINE_COMMITS = {
    "3ee6e6e8a50c24944c1983a75f5bf6dda9048f67",
    "c0ed4bfaae362621201f006e5ce7eef1c26e6047",
    "f66d78ff431e3d12b42fef55e81fca6c7d0e995a",
    "586e3270ba8eea1cf67235692d8e6a73595f2a3a",
    "40cfff2aba495320ee1bb4d8d179c362d909a46a",
    "af72046d0ce1d80417ccbc6ea150cbcb8c810505",
    "dfa5f948e4812376ebdd860d9b12e520c851488c",
    "65213d976eb3b4d835761c0b3f639581a533a27a",
    # 2026-07-18 audit report re-quoted the already-public TWS_USERID literal
    # before the report was untracked from this public repo. Immutable on
    # protected main; value tracked for rotation, not a new exposure.
    "9142de46a09e367483ed57e1a323f1141bd106cc",
    # 2026-08-13: main was rebase-merged and recreated the two commits above
    # under new SHAs. Blobs are identical to the pre-rebase originals
    # (32dd55bd == 9142de46's, 1d9a0955 == 65213d97's), so these carry no new
    # content -- only the commit identity changed.
    "6ec11003bb4521e7f7b677ffbd78b7fd4d09458a",
    "da857b8b19a6e5778bb86bf37465346dca8da89b",
}
EXAMPLE_BASELINE_COMMITS = {
    "3ee6e6e8a50c24944c1983a75f5bf6dda9048f67",
    "90daa01986c377ac20ba682021d77372fd030393",
    "25ef348f1eb882bdc4a7d735fd8e3206b2711c2e",
    "65213d976eb3b4d835761c0b3f639581a533a27a",
    # Same 2026-08-13 rebase; recreated fixture commit, identical blob.
    "da857b8b19a6e5778bb86bf37465346dca8da89b",
}


def _config_rules() -> dict[str, dict]:
    config = tomllib.loads(GITLEAKS_CONFIG.read_text(encoding="utf-8"))
    return {rule["id"]: rule for rule in config["rules"]}


def _rule_allowlist_commits(rule: dict) -> set[str]:
    allowlists = rule.get("allowlists", [])
    assert len(allowlists) == 1
    assert set(allowlists[0]) <= {"description", "commits"}
    return set(allowlists[0]["commits"])


def test_active_root_ci_runs_full_history_scan_with_cloud_policy() -> None:
    workflow = yaml.safe_load(ACTIVE_CI.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["secret-scan"]["steps"]
    checkout = next(step for step in steps if "actions/checkout" in step.get("uses", ""))
    assert checkout["with"]["fetch-depth"] == 0

    commands = "\n".join(str(step.get("run", "")) for step in steps)
    expected = (
        "gitleaks detect --source . --config cloud/.gitleaks.toml "
        "--redact --no-banner --verbose"
    )
    assert expected in " ".join(commands.split())


def test_dead_nested_cloud_workflow_is_removed() -> None:
    assert not DEAD_CLOUD_CI.exists()


def test_historical_exceptions_are_rule_scoped_and_commit_exact() -> None:
    rules = _config_rules()
    assert _rule_allowlist_commits(rules["literal-tws-credential-assignment"]) == (
        LITERAL_TWS_BASELINE_COMMITS
    )
    assert _rule_allowlist_commits(rules["credential-shaped-example"]) == (
        EXAMPLE_BASELINE_COMMITS
    )


def test_custom_rules_still_match_new_literals_but_not_empty_placeholders() -> None:
    rules = _config_rules()
    literal = re.compile(rules["literal-tws-credential-assignment"]["regex"])
    example = re.compile(rules["credential-shaped-example"]["regex"])

    # Build positive fixtures at runtime so the file never contains a full
    # TWS_* assignment or credential-example literal that full-history gitleaks
    # would treat as a real finding.
    password_assignment = "TWS_" + "PASSWORD" + "=" + "fresh_literal_credential"
    userid_assignment = "TWS_" + "USERID" + "='" + "fresh_literal_user" + "'"
    example_phrase = "credential " + "example: " + "fresh_literal_credential"

    assert literal.search(password_assignment)
    assert literal.search(userid_assignment)
    assert not literal.search("TWS_" + "PASSWORD" + "=")
    assert not literal.search("TWS_" + "USERID" + "=${TWS_USERID}")
    assert example.search(example_phrase)
