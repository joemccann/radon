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
    # 2026-08-30: PR #184's env-file fixture spelled a fake gateway password;
    # vetted as test data, rewritten to runtime construction afterwards.
    "020865adeff48b7182b7eb81d74a01fc70d00c82",
}
EXAMPLE_BASELINE_COMMITS = {
    "3ee6e6e8a50c24944c1983a75f5bf6dda9048f67",
    "90daa01986c377ac20ba682021d77372fd030393",
    "25ef348f1eb882bdc4a7d735fd8e3206b2711c2e",
    "65213d976eb3b4d835761c0b3f639581a533a27a",
    # Same 2026-08-13 rebase; recreated fixture commit, identical blob.
    "da857b8b19a6e5778bb86bf37465346dca8da89b",
}


# generic-api-key is a gitleaks DEFAULT rule, so its exceptions live in a
# global [[allowlists]] entry scoped with targetRules rather than in a
# [[rules]] block.
GENERIC_API_KEY_BASELINE_COMMITS = {
    # 2026-08-31 (#212): the weekend-redactor test spelled a fake UW token to
    # prove the redactor scrubs env values; merged before the scan ran. The
    # fixture was rewritten to runtime construction afterwards.
    "38eeecb91eedb3b90966821bff12d5b240d89708",
}


def _config() -> dict:
    return tomllib.loads(GITLEAKS_CONFIG.read_text(encoding="utf-8"))


def _config_rules() -> dict[str, dict]:
    config = tomllib.loads(GITLEAKS_CONFIG.read_text(encoding="utf-8"))
    return {rule["id"]: rule for rule in config["rules"]}


def _rule_allowlist_commits(rule: dict) -> set[str]:
    allowlists = rule.get("allowlists", [])
    assert len(allowlists) == 1
    assert set(allowlists[0]) <= {"description", "commits"}
    return set(allowlists[0]["commits"])


def _secret_scan_job() -> dict:
    workflow = yaml.safe_load(ACTIVE_CI.read_text(encoding="utf-8"))
    job = workflow["jobs"]["secret-scan"]
    assert job.get("continue-on-error") is not True
    return job


def _scan_step(job: dict) -> dict:
    return next(
        step
        for step in job["steps"]
        if "gitleaks detect" in str(step.get("run", ""))
    )


def test_active_root_ci_scopes_pr_gitleaks_to_merge_base_head() -> None:
    job = _secret_scan_job()
    steps = job["steps"]
    checkout = next(step for step in steps if "actions/checkout" in step.get("uses", ""))
    assert checkout["with"]["fetch-depth"] == 0

    scan = _scan_step(job)
    assert scan.get("continue-on-error") is not True
    env = scan.get("env") or {}
    assert "github.event_name" in str(env.get("EVENT_NAME", ""))
    assert "github.event.pull_request.base.sha" in str(env.get("PR_BASE", ""))
    assert "github.event.pull_request.head.sha" in str(env.get("PR_HEAD", ""))
    assert "github.event.before" in str(env.get("PUSH_BEFORE", ""))
    assert "github.sha" in str(env.get("PUSH_HEAD", ""))

    script = scan["run"]
    assert 'EVENT_NAME" = "pull_request"' in script
    assert "git merge-base" in script
    assert "log_opts=" in script
    assert "${merge_base}..${PR_HEAD}" in script
    assert 'ensure_commit "$PUSH_HEAD"' in script
    assert 'ensure_commit "$PUSH_BEFORE"' in script
    assert 'log_opts="${PUSH_BEFORE}..${PUSH_HEAD}"' in script
    assert 'log_opts="HEAD"' not in script
    # A missing non-zero before must fail the job, not scan only the tip.
    assert '[ "$PUSH_BEFORE" = "$zero" ] || ! git cat-file -e "${PUSH_BEFORE}^{commit}"' not in script
    collapsed = " ".join(script.split())
    expected = (
        "gitleaks detect --source . --config cloud/.gitleaks.toml "
        "--redact --no-banner --verbose --log-opts="
    )
    assert expected in collapsed


def test_this_branch_has_no_static_tws_credential_assignments() -> None:
    """HEAD fixtures must runtime-concatenate TWS_* so the assignment never
    sits in the tree as a gitleaks hit. Sanitize/tests still see the value
    after join.
    """
    rules = _config_rules()
    literal = re.compile(rules["literal-tws-credential-assignment"]["regex"])
    roots = (
        REPO_ROOT / "scripts" / "tests",
        REPO_ROOT / "cloud" / "tests",
        REPO_ROOT / "scripts" / "github_pr_output.py",
    )
    hits: list[str] = []
    for root in roots:
        paths = [root] if root.is_file() else sorted(root.rglob("*.py"))
        for path in paths:
            text = path.read_text(encoding="utf-8")
            if literal.search(text):
                hits.append(str(path.relative_to(REPO_ROOT)))
    assert hits == []


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


def test_global_commit_exceptions_are_rule_scoped_and_commit_exact() -> None:
    config = _config()
    assert "allowlist" not in config, (
        "the singular [allowlist] table cannot coexist with [[allowlists]]; "
        "gitleaks refuses to load the config"
    )
    exceptions = [a for a in config["allowlists"] if a.get("commits")]
    assert len(exceptions) == 1
    entry = exceptions[0]
    # Without targetRules a commit exception blanket-allows EVERY rule for
    # that SHA, including the custom TWS ones.
    assert entry["targetRules"] == ["generic-api-key"]
    assert set(entry["commits"]) == GENERIC_API_KEY_BASELINE_COMMITS


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
