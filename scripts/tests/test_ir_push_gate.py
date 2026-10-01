"""2026-09-30: IR PRs went public with GROK_PAGE_AUTOPUSH=0, one carrying
private account and execution ids from the page journal.

Every automated push/PR now fails closed: no push or PR unless
GROK_PAGE_AUTOPUSH is truthy, and none when the commits, diff or PR text
carry a private identifier. Refused, never silently redacted. All ids here
are invented and assembled at runtime so no literal id shape sits in git.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import ir_ensure_pr as ir
import ir_push_gate as gate

ACCOUNT = "U" + "1234567"
PAPER_ACCOUNT = "DU" + "7654321"
FA_ACCOUNT = "F" + "1000001"
FLEX_EXEC = "98765" + "43210987"
IB_EXEC = "0000abcd" + ".1234ef56" + ".01.01"
GH_TOKEN = "gh" + "p_" + "Z" * 36

VALID_BODY = (
    "## What broke\n\nThe relay unit failed with Result=exit-code at 12:00Z.\n\n"
    "## Root cause\n\nA ledger read timeout escaped run_cycle and exited 1.\n\n"
    "## What changed\n\n- relay.py: treat the ledger timeout as non-fatal.\n\n"
    "## How it was verified\n\nFocused relay pytest passed locally, 9 cases.\n\n"
    "## Risk and rollback\n\nLow; revert the commit to restore the old path.\n\n"
    "## Still open\n\nNothing beyond watching the next scheduled relay run.\n"
)


class TestAutopushFlag:
    @pytest.mark.parametrize("raw", [None, "", "0", "false", "No", " off "])
    def test_unset_or_falsey_is_off(self, raw):
        env = {} if raw is None else {"GROK_PAGE_AUTOPUSH": raw}
        assert gate.autopush_enabled(env) is False
        with pytest.raises(gate.IrPushRefused, match="GROK_PAGE_AUTOPUSH"):
            gate.require_autopush(env)

    @pytest.mark.parametrize("raw", ["1", "true", "yes", "on"])
    def test_truthy_is_on(self, raw):
        assert gate.autopush_enabled({"GROK_PAGE_AUTOPUSH": raw}) is True


class TestFindPrivateIdentifiers:
    @pytest.mark.parametrize(
        "text, kind",
        [
            (f"account {ACCOUNT} flat", "ib_account_id"),
            (f"paper {PAPER_ACCOUNT}", "ib_account_id"),
            (f"advisor {FA_ACCOUNT}", "ib_account_id"),
            (f'"ibExecID": "{FLEX_EXEC}"', "flex_exec_id"),
            (f"execId={IB_EXEC}", "ib_exec_id"),
            (f"token {GH_TOKEN}", "credential [redacted-key]"),
            ("url " + "libsql" + "://db-example.turso.io", "credential [redacted-db-url]"),
        ],
        ids=["account", "paper", "advisor", "flex", "ib_exec", "gh_token", "db_url"],
    )
    def test_private_shapes_are_found(self, text, kind):
        assert kind in gate.find_private_identifiers(text)

    @pytest.mark.parametrize(
        "text",
        [
            "commit 3b990bb8496e24785a14a4df32e79a4e and sha "
            "cb94c6cef485666e0e9e737ea4cd4cc52f28bdba",
            "https://github.com/joemccann/radon/actions/runs/36361801938",
            "U12345 is five digits; 123456789 is nine",
            "token = uuid.uuid4().hex",
            "pi is 3.14159265358979",
            "USD1234567 and XU1234567 are not account ids",
        ],
    )
    def test_ordinary_text_is_clean(self, text):
        assert gate.find_private_identifiers(text) == []

    def test_findings_never_carry_the_value(self):
        found = gate.find_private_identifiers(f"{ACCOUNT} {FLEX_EXEC} {GH_TOKEN}")
        blob = json.dumps(found)
        for value in (ACCOUNT, FLEX_EXEC, GH_TOKEN):
            assert value not in blob


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.name", "t")
    _git(tmp_path, "config", "user.email", "t@example.invalid")
    (tmp_path / "app.py").write_text(f"# removed later: {ACCOUNT}\nx = 1\n")
    _git(tmp_path, "add", "app.py")
    _git(tmp_path, "commit", "-q", "-m", "init")
    _git(tmp_path, "checkout", "-q", "-b", "fix/relay")
    return tmp_path


def _commit(repo: Path, content: str, message: str) -> None:
    (repo / "app.py").write_text(content)
    _git(repo, "add", "app.py")
    _git(repo, "commit", "-q", "-m", message)


class TestScanCommitRange:
    def test_identifier_removed_before_the_tip_still_refuses_publication(self, repo):
        # Both commits are pushed, even though the final tree is clean.
        _commit(repo, f"x = 2\nEXEC = '{IB_EXEC}'\n", "fix: inspect execution")
        _commit(repo, "x = 3\n", "fix: remove diagnostic")

        with pytest.raises(gate.IrPushRefused, match="ib_exec_id") as exc:
            gate.check_publish(
                repo=repo, base="main", ref="fix/relay",
                env={"GROK_PAGE_AUTOPUSH": "1"},
            )
        assert IB_EXEC not in str(exc.value)

    def test_deleted_file_in_an_earlier_commit_is_still_scanned(self, repo):
        report = repo / "diagnostic.txt"
        report.write_text(f"account={PAPER_ACCOUNT}\n")
        assert _git(repo, "add", "diagnostic.txt").returncode == 0
        assert _git(repo, "commit", "-qm", "fix: save diagnostic").returncode == 0
        assert _git(repo, "rm", "diagnostic.txt").returncode == 0
        assert _git(repo, "commit", "-qm", "fix: remove diagnostic").returncode == 0

        found = gate.scan_commit_range(repo, "main", "fix/relay")
        assert "ib_account_id in diff of diagnostic.txt" in found
        assert PAPER_ACCOUNT not in json.dumps(found)

    def test_merge_resolution_removed_before_tip_is_still_scanned(self, repo):
        assert _git(repo, "checkout", "-qb", "side", "main").returncode == 0
        _commit(repo, "x = 2\n", "fix: side work")
        assert _git(repo, "checkout", "-q", "fix/relay").returncode == 0
        _commit(repo, "x = 3\n", "fix: branch work")
        # The identifier exists only in the merge result, not either parent.
        assert _git(repo, "merge", "--no-ff", "--no-commit", "-s", "ours", "side").returncode == 0
        _commit(repo, f"x = 3\nEXEC = '{IB_EXEC}'\n", "fix: resolve merge")
        _commit(repo, "x = 4\n", "fix: remove diagnostic")
        assert "ib_exec_id in diff of app.py" in gate.scan_commit_range(repo, "main", "fix/relay")

    def test_clean_branch_has_no_findings(self, repo):
        _commit(repo, "x = 2\n", "fix: relay\n\n" + VALID_BODY)
        assert gate.scan_commit_range(repo, "main", "fix/relay") == []

    def test_removed_lines_are_not_findings(self, repo):
        """Deleting an id that is already on the base publishes nothing new."""
        _commit(repo, "x = 1\n", "fix: drop a comment")
        assert gate.scan_commit_range(repo, "main", "fix/relay") == []

    def test_added_line_and_message_are_findings(self, repo):
        _commit(
            repo,
            f"x = 2\nEXEC = '{IB_EXEC}'\n",
            f"fix: relay\n\nJournal row for {PAPER_ACCOUNT} exec {FLEX_EXEC}",
        )
        found = gate.scan_commit_range(repo, "main", "fix/relay")
        assert "ib_exec_id in diff of app.py" in found
        assert any(f.startswith("ib_account_id in commit message") for f in found)
        assert any(f.startswith("flex_exec_id in commit message") for f in found)
        assert not any(v in json.dumps(found) for v in (IB_EXEC, PAPER_ACCOUNT, FLEX_EXEC))


class FakeProc(SimpleNamespace):
    def __init__(self, returncode=0, stdout="", stderr=""):
        super().__init__(returncode=returncode, stdout=stdout, stderr=stderr)


def _gh_runner(calls: list):
    def run(argv, **_kw):
        calls.append(list(argv))
        if argv[1:3] == ["auth", "status"]:
            return FakeProc()
        if argv[1:3] == ["pr", "list"]:
            return FakeProc(stdout="[]")
        if argv[1:3] == ["pr", "create"]:
            return FakeProc(stdout="https://github.com/x/y/pull/9\n")
        raise AssertionError(argv)
    return run


class TestEnsurePrFailsClosed:
    @pytest.mark.parametrize("raw", [None, "0"])
    def test_no_pr_when_autopush_is_off(self, monkeypatch, raw):
        if raw is None:
            monkeypatch.delenv("GROK_PAGE_AUTOPUSH", raising=False)
        else:
            monkeypatch.setenv("GROK_PAGE_AUTOPUSH", raw)
        calls: list = []
        with pytest.raises(ir.IrEnsurePrError, match="GROK_PAGE_AUTOPUSH"):
            ir.ensure_pr(
                head="fix/relay", issue="Relay.", fix="Relay.", body=VALID_BODY,
                runner=_gh_runner(calls), gh_bin="gh",
            )
        assert calls == []

    @pytest.mark.parametrize(
        "leak",
        [ACCOUNT, FLEX_EXEC, IB_EXEC, GH_TOKEN],
        ids=["account", "flex_exec", "ib_exec", "gh_token"],
    )
    def test_private_id_in_body_refuses_not_redacts(self, monkeypatch, leak):
        monkeypatch.setenv("GROK_PAGE_AUTOPUSH", "1")
        calls: list = []
        with pytest.raises(ir.IrEnsurePrError, match="private identifiers") as exc:
            ir.ensure_pr(
                head="fix/relay", issue="Relay.", fix="Relay.",
                body=VALID_BODY + f"\nJournal: {leak}\n",
                runner=_gh_runner(calls), gh_bin="gh",
            )
        assert leak not in str(exc.value)
        assert calls == []

    def test_placeholder_body_is_refused(self, monkeypatch):
        """The #823/#824 body: issue and fix both 'grok incident fix on ...'."""
        monkeypatch.setenv("GROK_PAGE_AUTOPUSH", "1")
        calls: list = []
        with pytest.raises(ir.IrEnsurePrError, match="description invalid"):
            ir.ensure_pr(
                head="fix/relay",
                issue="grok incident fix on fix/relay",
                fix="grok incident fix on fix/relay",
                runner=_gh_runner(calls), gh_bin="gh",
            )
        assert not any(c[1:3] == ["pr", "create"] for c in calls)

    def test_real_body_creates(self, monkeypatch):
        monkeypatch.setenv("GROK_PAGE_AUTOPUSH", "1")
        calls: list = []
        out = ir.ensure_pr(
            head="fix/relay", issue="Relay.", fix="Relay.", body=VALID_BODY,
            runner=_gh_runner(calls), gh_bin="gh",
        )
        assert out["action"] == "created"
        create = next(c for c in calls if c[1:3] == ["pr", "create"])
        assert create[create.index("--body") + 1] == VALID_BODY

    def test_commit_range_is_scanned_when_repo_given(self, monkeypatch, repo):
        monkeypatch.setenv("GROK_PAGE_AUTOPUSH", "1")
        _commit(repo, f"x = 2  # {ACCOUNT}\n", "fix: relay\n\n" + VALID_BODY)
        calls: list = []
        with pytest.raises(ir.IrEnsurePrError, match="ib_account_id in diff"):
            ir.ensure_pr(
                head="fix/relay", issue="Relay.", fix="Relay.", body=VALID_BODY,
                runner=None, gh_bin="gh", which=lambda _n: "gh",
                repo_root=repo, scan_base="main",
            )


class TestCliBuildsARealBody:
    def test_cli_body_comes_from_the_commit_not_a_placeholder(self, monkeypatch, capsys):
        monkeypatch.setenv("GROK_PAGE_AUTOPUSH", "1")
        monkeypatch.setattr(
            ir, "_git_stdout", lambda *_a, **_k: "fix: relay\n\n" + VALID_BODY
        )
        seen: dict = {}
        monkeypatch.setattr(
            ir, "ensure_pr",
            lambda **k: seen.update(k) or {"action": "created", "url": "https://x/pull/1"},
        )
        assert ir.main(["--head", "fix/relay"]) == 0
        assert "## What broke" in seen["body"]
        assert "Incident-response fix." not in json.dumps(seen, default=str)
        assert seen["issue"].startswith("The relay unit failed")

    def test_cli_refuses_a_placeholder_commit(self, monkeypatch, capsys):
        monkeypatch.setenv("GROK_PAGE_AUTOPUSH", "1")
        monkeypatch.setattr(
            ir, "_git_stdout", lambda *_a, **_k: "grok incident fix on fix/relay\n"
        )
        monkeypatch.setattr(
            ir, "ensure_pr", lambda **k: pytest.fail("must not reach ensure_pr")
        )
        assert ir.main(["--head", "fix/relay"]) == 2
        assert "description invalid" in capsys.readouterr().err
