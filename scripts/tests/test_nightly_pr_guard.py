"""Nightly agents cannot accidentally publish audit-only work via raw gh."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import shlex
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("nightly_pr_guard", ROOT / "scripts/nightly_pr_guard.py")
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)
WRAPPERS = ("reliability_weekend", "testing_weekend", "ci_performance_nightly", "documentation_nightly", "security_nightly", "security_deepsec_nightly")


@pytest.mark.parametrize("args", [
    ["pr", "checks", "1"], ["issue", "comment", "1", "--body", "done"],
    ["api", "repos/a/b/pulls"], ["api", "repos/a/b/pulls/1", "-X", "PATCH", "-f", "body=updated"],
    ["api", "graphql", "-f", "query=query { viewer { login } }"],
])
def test_reporting_reads_and_updates_pass_without_git_calls(args):
    def forbidden(*a, **kw):
        pytest.fail("non-creation command queried git")
    mod.guard(args, run=forbidden)


@pytest.mark.parametrize("args", [
    ["api", "repos/a/b/pulls", "--method", "POST"],
    ["api", "/repos/a/b/pulls", "-f", "head=testing/today"],
    ["api", "https://api.github.com/repos/a/b/pulls", "--input=body.json"],
    ["api", "graphql", "-f", "query=mutation { createPullRequest(input:{}) { id } }"],
    ["api", "graphql", "--input", "-"],
    ["api", "https://api.github.com/graphql", "--input", "body.json"],
    ["api", "graphql", "-f", "query=@body.graphql"],
])
def test_alternative_creation_routes_fail_closed(args):
    with pytest.raises(mod.Refused, match="API creation"):
        mod.guard(args)


@pytest.mark.parametrize("head_args", [[], ["--head", "owner:branch"], ["-Hbranch", "-Bother"], ["-Hbranch", "--repo=a/b"]])
def test_ambiguous_remote_or_target_is_rejected(head_args):
    with pytest.raises(mod.Refused):
        mod.guard(["pr", "create", *head_args])


@pytest.mark.parametrize("verdict,refused", [(0, False), (3, True), (1, True)])
def test_creation_classifies_remote_head_and_fails_closed(verdict, refused):
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, verdict if "check" in args else 0, "", "")
    if refused:
        with pytest.raises(mod.Refused):
            mod.guard(["pr", "create", "--head=testing/today", "--base=main"], run=run)
    else:
        mod.guard(["pr", "create", "--head=testing/today", "--base=main"], run=run)
    assert calls[1][-1] == "+refs/heads/testing/today:refs/remotes/origin/testing/today"
    assert calls[-1][-4:] == ["--base", "origin/main", "--head", "origin/testing/today"]


def test_fetch_failure_never_reaches_publication_check():
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        if "fetch" in args:
            raise subprocess.CalledProcessError(1, args)
        return subprocess.CompletedProcess(args, 0)
    with pytest.raises(subprocess.CalledProcessError):
        mod.guard(["pr", "create", "-Hbranch"], run=run)
    assert len(calls) == 2


@pytest.mark.parametrize("wrapper", WRAPPERS)
@pytest.mark.parametrize("verdict", [0, 3, 1])
def test_each_runner_installs_an_effective_guard(tmp_path, wrapper, verdict):
    """Execute actual bash installer + gh shim, with a deterministic git oracle."""
    source = (ROOT / f"scripts/{wrapper}.sh").read_text()
    installer = source.split("install_nightly_pr_guard() {", 1)[1].split("\nlaunch_round() {", 1)[0]
    installer = "install_nightly_pr_guard() {" + installer
    assert "  install_nightly_pr_guard\n" in source
    assert 'local PATH="$NIGHTLY_PR_GUARD_DIR:$PATH"' in source
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    called = tmp_path / "called"
    gh = bin_dir / "real-gh"
    gh.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> {shlex.quote(str(called))}\n")
    gh.chmod(0o700)
    # Trusted source is served by git-show, not by files in the checkout.
    oracle = tmp_path / "nightly_publish.py"
    oracle.write_text(f"raise SystemExit({verdict})\n")
    git = bin_dir / "git"
    git.write_text(
        "#!/bin/sh\ncase \"$*\" in\n"
        f" *show*nightly_pr_guard.py*) cat {shlex.quote(str(ROOT / 'scripts/nightly_pr_guard.py'))} ;;\n"
        f" *show*nightly_publish.py*) cat {shlex.quote(str(oracle))} ;;\n"
        " *) exit 0 ;;\nesac\n"
    )
    git.chmod(0o700)
    (bin_dir / "python3.13").symlink_to(sys.executable)
    driver = tmp_path / "driver.sh"
    driver.write_text("set -euo pipefail\n" + installer + "\n" +
        f"GH_BIN={shlex.quote(str(gh))}\nREPO={shlex.quote(str(tmp_path))}\nLOOP_SLUG=testing\n" +
        "install_nightly_pr_guard\ntrap 'rm -rf -- \"$NIGHTLY_PR_GUARD_DIR\"' EXIT\n" +
        'export PATH="$NIGHTLY_PR_GUARD_DIR:$PATH"\ngh pr checks 12\ngh pr create --base main --head testing/today\n')
    env = {**os.environ, "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"]}
    result = subprocess.run(["bash", str(driver)], env=env, capture_output=True, text=True, timeout=15)
    lines = called.read_text().splitlines()
    assert lines[0] == "pr checks 12"
    assert (result.returncode == 0) == (verdict == 0), result.stderr
    assert len(lines) == (2 if verdict == 0 else 1)


def test_main_requires_absolute_real_gh(monkeypatch, capsys):
    monkeypatch.setenv("RADON_NIGHTLY_REAL_GH", "gh")
    assert mod.main(["pr", "create"]) == 1
    assert "absolute gh" in capsys.readouterr().err


def test_main_does_not_exec_after_rejection(monkeypatch, capsys):
    monkeypatch.setenv("RADON_NIGHTLY_REAL_GH", "/bin/gh")
    monkeypatch.setattr(mod.os, "execv", lambda *a: pytest.fail("gh invoked after rejection"))
    assert mod.main(["pr", "create"]) == 1
    assert "explicit origin" in capsys.readouterr().err


def test_main_preserves_arguments_for_normal_reporting(monkeypatch):
    monkeypatch.setenv("RADON_NIGHTLY_REAL_GH", "/bin/gh")
    calls = []
    monkeypatch.setattr(mod.os, "execv", lambda *a: calls.append(a))
    mod.main(["issue", "comment", "1", "--body", "nightly complete"])
    assert calls == [("/bin/gh", ["/bin/gh", "issue", "comment", "1", "--body", "nightly complete"])]


@pytest.mark.parametrize("args", [
    ["pr", "merge", "1"], ["pr", "-R", "a/b", "merge", "1", "--squash"], ["-R", "a/b", "pr", "merge", "1"],
    ["api", "repos/a/b/pulls/1/merge", "-X", "PUT"],
    ["api", "graphql", "-f", "query=mutation { mergePullRequest(input:{}) { clientMutationId } }"],
    ["api", "graphql", "-f", "query=mutation { enablePullRequestAutoMerge(input:{}) { clientMutationId } }"],
])
def test_nightly_loops_never_merge(args):
    with pytest.raises(mod.Refused, match="never merge"):
        mod.guard(args)


@pytest.mark.parametrize("loop", ["security", "security-deepsec"])
@pytest.mark.parametrize("args", [
    ["issue", "comment", "1", "--body", "x"], ["issue", "-R", "a/b", "create", "-t", "x"], ["issue", "edit", "1"],
    ["api", "repos/a/b/issues/1/comments", "-f", "body=x"], ["api", "repos/a/b/issues", "-X", "POST"],
])
def test_security_loops_cannot_write_public_issues(loop, args):
    with pytest.raises(mod.Refused, match="wrapper"):
        mod.guard(args, loop=loop)


@pytest.mark.parametrize("loop", ["", "reliability", "testing"])
def test_other_loops_keep_issue_reporting(loop):
    mod.guard(["issue", "comment", "1", "--body", "done"], loop=loop)
    mod.guard(["issue", "view", "1"], loop="security")


def test_main_reads_loop_from_wrapper_env(monkeypatch):
    monkeypatch.setenv("RADON_NIGHTLY_REAL_GH", "/nonexistent/gh")
    monkeypatch.setenv("RADON_NIGHTLY_LOOP", "security-deepsec")
    assert mod.main(["issue", "comment", "1", "--body", "x"]) == 1


# gh (Cobra) consumes the next token for every non-bool flag, wherever the
# flag sits, so a value must never be read as a subcommand or api endpoint.
@pytest.mark.parametrize("args", [
    ["api", "-X", "PUT", "repos/o/r/pulls/5/merge"], ["api", "--method", "PUT", "repos/o/r/pulls/5/merge"],
    ["api", "-H", "Accept:x", "repos/o/r/pulls/5/merge", "-X", "PUT"],
    ["pr", "-t", "subj", "merge", "5", "--squash"], ["pr", "--body", "x", "merge", "5"], ["-t", "subj", "pr", "merge", "5"],
])
def test_merge_is_refused_when_flag_values_precede_the_action(args):
    with pytest.raises(mod.Refused, match="never merge"):
        mod.guard(args)


@pytest.mark.parametrize("loop", ["security", "security-deepsec"])
@pytest.mark.parametrize("args", [
    ["issue", "-b", "x", "comment", "5"], ["issue", "--body", "x", "comment", "5"],
    ["api", "-X", "POST", "repos/o/r/issues/5/comments", "-f", "body=x"],
    ["api", "-f", "body=x", "repos/o/r/issues/5/comments"],
])
def test_security_issue_writes_are_refused_when_flag_values_precede_the_action(loop, args):
    with pytest.raises(mod.Refused, match="wrapper"):
        mod.guard(args, loop=loop)


@pytest.mark.parametrize("args", [
    ["pr", "--title", "t", "create", "--head", "h", "--base", "main"],
    ["pr", "-t", "t", "create", "--head", "h", "--base", "main", "-R", "evil/r"],
])
def test_flagged_pr_create_still_goes_through_the_publication_checks(args):
    calls = []
    def run(a, **kwargs):
        calls.append(a)
        return subprocess.CompletedProcess(a, 3 if "check" in a else 0, "", "")
    with pytest.raises(mod.Refused):
        mod.guard(args, run=run)


@pytest.mark.parametrize("args", [["alias", "set", "m", "pr merge"], ["alias", "import", "-"], ["alias", "set", "--shell", "m", "gh pr merge"]])
def test_aliases_cannot_be_defined(args):
    with pytest.raises(mod.Refused, match="alias"):
        mod.guard(args)


@pytest.mark.parametrize("args", [
    ["pr", "view", "5"], ["pr", "checks", "https://github.com/o/r/pull/5", "--watch"],
    ["pr", "-R", "o/r", "view", "5"], ["api", "-X", "PATCH", "repos/o/r/pulls/5", "-f", "body=x"],
    ["alias", "list"],
])
def test_reads_and_updates_with_leading_flags_still_pass(args):
    mod.guard(args, run=lambda *a, **k: pytest.fail("non-creation command queried git"), loop="security")
