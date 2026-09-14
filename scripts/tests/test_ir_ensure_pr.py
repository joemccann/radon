"""Incident-response open-PR hook: create if missing, never merge, fail closed."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import github_pr_output as pr_fmt
import grok_page_responder as responder
import ir_ensure_pr as ir

REPO = Path(__file__).resolve().parents[2]
SKILL = REPO / ".claude" / "skills" / "incident-response" / "SKILL.md"
COMMAND = REPO / ".claude" / "commands" / "incident.md"
GROK_DOC = REPO / "docs" / "grok-page-responder.md"
RUNBOOK = REPO / "docs" / "incident-runbook.md"
WRAPPER = REPO / "scripts" / "ir_open_pr.sh"


class FakeProc(SimpleNamespace):
    def __init__(self, returncode=0, stdout="", stderr=""):
        super().__init__(returncode=returncode, stdout=stdout, stderr=stderr)


class FakeRunner:
    def __init__(self, handlers: dict[tuple[str, ...], FakeProc] | None = None):
        self.calls: list[list[str]] = []
        self.handlers = handlers or {}

    def __call__(self, argv, **_kwargs):
        cmd = list(argv)
        self.calls.append(cmd)
        key = tuple(cmd)
        for prefix, proc in self.handlers.items():
            if key[: len(prefix)] == prefix:
                return proc
        return FakeProc(1, stderr="unexpected: " + " ".join(cmd))


def _page(**overrides) -> dict:
    page = {
        "page_id": "page-leap",
        "service": "radon-leap",
        "severity": "P1",
        "kind": "unit",
        "message_excerpt": "<untrusted-excerpt>PermissionError</untrusted-excerpt>",
        "paged_at": "2026-09-14T12:00:00Z",
        "status": "pending",
    }
    page.update(overrides)
    return page


class TestIrBranchPrefix:
    @pytest.mark.parametrize(
        "name",
        [
            "fix/leap-reports-permission-502",
            "fix/a",
            "origin/fix/leap-reports-permission-502",
        ],
    )
    def test_accepted(self, name):
        assert ir.is_ir_branch(name) is True

    @pytest.mark.parametrize(
        "name",
        [
            "",
            "fix",
            "fix/",
            "main",
            "feature/leap",
            "hotfix/leap",
            "factory/leap",
            "fix/../main",
            "fix/foo bar",
        ],
    )
    def test_rejected(self, name):
        assert ir.is_ir_branch(name) is False


class TestIrPrCopy:
    def test_title_is_plain_language(self):
        title = ir.format_ir_pr_title(
            issue="Leap reports died on a PermissionError 502."
        )
        assert title.startswith("IR: ")
        assert "PermissionError" in title
        assert "\n" not in title
        assert len(title) <= pr_fmt.GITHUB_PR_TITLE_MAX

    def test_title_includes_incident_id(self):
        title = ir.format_ir_pr_title(
            issue="Leap reports died on a PermissionError 502.",
            incident_id="20260914T120000Z-leap",
        )
        assert title.startswith("IR 20260914T120000Z-leap:")

    def test_body_reuses_nightly_headings_and_links_the_incident(self):
        body = ir.format_ir_pr_body(
            issue="Leap reports died on a PermissionError 502.",
            fix="Wrote reports under the cache dir the unit can write.",
            incident_id="20260914T120000Z-leap",
            case_id="leap-reports-permission",
        )
        assert "## Issue discovered" in body
        assert "## What was done to fix it" in body
        assert "## Next" in body
        assert "20260914T120000Z-leap" in body
        assert "docs/incident-runbook.md#leap-reports-permission" in body
        assert ir.DEFAULT_NEXT in body
        assert "never merges" in body.lower()
        assert pr_fmt.GREEN_DEPLOYMENT not in body

    def test_explicit_next_replaces_the_default(self):
        body = ir.format_ir_pr_body(
            issue="A.",
            fix="B.",
            next_action="Joe merges after the focused leap pytest is green.",
        )
        assert "Joe merges after the focused leap pytest is green." in body
        assert ir.DEFAULT_NEXT not in body


class TestEnsurePr:
    def test_creates_when_no_open_pr(self):
        runner = FakeRunner({
            ("gh", "auth", "status"): FakeProc(0, stdout="Logged in"),
            ("gh", "pr", "list"): FakeProc(0, stdout="[]\n"),
            ("gh", "pr", "create"): FakeProc(
                0, stdout="https://github.com/joemccann/radon/pull/435\n"
            ),
        })
        result = ir.ensure_pr(
            head="fix/leap-reports-permission-502",
            issue="Leap reports died on a PermissionError 502.",
            fix="Wrote reports under the cache dir the unit can write.",
            incident_id="20260914T120000Z-leap",
            case_id="leap-reports-permission",
            runner=runner,
            gh_bin="gh",
        )
        assert result["action"] == "created"
        assert result["url"] == "https://github.com/joemccann/radon/pull/435"
        assert result["head"] == "fix/leap-reports-permission-502"
        create = [c for c in runner.calls if c[:3] == ["gh", "pr", "create"]]
        assert len(create) == 1
        assert "--base" in create[0] and "main" in create[0]
        assert "--head" in create[0]
        assert "fix/leap-reports-permission-502" in create[0]
        assert "--title" in create[0]
        assert "--body" in create[0]

    def test_noop_when_pr_already_open(self):
        runner = FakeRunner({
            ("gh", "auth", "status"): FakeProc(0),
            ("gh", "pr", "list"): FakeProc(
                0,
                stdout=json.dumps([{
                    "number": 434,
                    "url": "https://github.com/joemccann/radon/pull/434",
                    "title": "IR: leap",
                }]) + "\n",
            ),
        })
        result = ir.ensure_pr(
            head="fix/leap-reports-permission-502",
            issue="Leap reports died on a PermissionError 502.",
            fix="Wrote reports under the cache dir the unit can write.",
            runner=runner,
            gh_bin="gh",
        )
        assert result["action"] == "exists"
        assert result["url"].endswith("/434")
        assert not any(c[:3] == ["gh", "pr", "create"] for c in runner.calls)

    def test_create_race_that_already_exists_is_a_noop(self):
        runner = FakeRunner({
            ("gh", "auth", "status"): FakeProc(0),
            ("gh", "pr", "list"): FakeProc(0, stdout="[]\n"),
            ("gh", "pr", "create"): FakeProc(
                1,
                stderr=(
                    "a pull request for branch "
                    '"fix/leap-reports-permission-502" already exists:\n'
                    "https://github.com/joemccann/radon/pull/434\n"
                ),
            ),
        })
        result = ir.ensure_pr(
            head="fix/leap-reports-permission-502",
            issue="Leap reports died on a PermissionError 502.",
            fix="Wrote reports under the cache dir the unit can write.",
            runner=runner,
            gh_bin="gh",
        )
        assert result["action"] == "exists"
        assert result["url"].endswith("/434")

    def test_never_merges(self):
        runner = FakeRunner({
            ("gh", "auth", "status"): FakeProc(0),
            ("gh", "pr", "list"): FakeProc(0, stdout="[]\n"),
            ("gh", "pr", "create"): FakeProc(
                0, stdout="https://github.com/joemccann/radon/pull/1\n"
            ),
        })
        ir.ensure_pr(
            head="fix/example",
            issue="A.",
            fix="B.",
            runner=runner,
            gh_bin="gh",
        )
        assert not any("merge" in c for c in runner.calls)

    def test_refuses_a_non_ir_branch(self):
        with pytest.raises(ir.IrEnsurePrError, match="fix/"):
            ir.ensure_pr(
                head="main",
                issue="A.",
                fix="B.",
                runner=FakeRunner(),
                gh_bin="gh",
            )

    def test_fail_closed_when_gh_missing(self):
        with pytest.raises(ir.IrEnsurePrError, match="gh CLI") as excinfo:
            ir.ensure_pr(
                head="fix/example",
                issue="A.",
                fix="B.",
                runner=FakeRunner(),
                gh_bin=None,
                which=lambda _name: None,
            )
        assert "Branch-only is not a ship" in str(excinfo.value)
        assert excinfo.value.code == 2

    def test_fail_closed_when_gh_unauthenticated(self):
        runner = FakeRunner({
            ("gh", "auth", "status"): FakeProc(1, stderr="not logged in"),
        })
        with pytest.raises(ir.IrEnsurePrError, match="authenticated") as excinfo:
            ir.ensure_pr(
                head="fix/example",
                issue="A.",
                fix="B.",
                runner=runner,
                gh_bin="gh",
            )
        assert "Branch-only is not a ship" in str(excinfo.value)
        assert excinfo.value.code == 2

    def test_fail_closed_when_pat_lacks_pull_requests_write(self):
        runner = FakeRunner({
            ("gh", "auth", "status"): FakeProc(0),
            ("gh", "pr", "list"): FakeProc(
                1,
                stderr="GraphQL: Resource not accessible by personal access token (repository.pullRequests)",
            ),
        })
        with pytest.raises(ir.IrEnsurePrError, match="pull_requests") as excinfo:
            ir.ensure_pr(
                head="fix/example",
                issue="A.",
                fix="B.",
                runner=runner,
                gh_bin="gh",
            )
        msg = str(excinfo.value)
        assert "Contents" in msg
        assert "Administration" in msg
        assert "Branch-only is not a ship" in msg
        assert excinfo.value.code == 2


class TestInferIrHead:
    def test_prefers_current_fix_branch(self, tmp_path):
        runner = FakeRunner({
            ("git", "rev-parse", "--abbrev-ref", "HEAD"): FakeProc(
                0, stdout="fix/leap-reports-permission-502\n"
            ),
        })
        assert ir.infer_ir_head(tmp_path, runner=runner) == (
            "fix/leap-reports-permission-502"
        )

    def test_falls_back_to_newest_local_fix_branch(self, tmp_path):
        runner = FakeRunner({
            ("git", "rev-parse", "--abbrev-ref", "HEAD"): FakeProc(
                0, stdout="main\n"
            ),
            ("git", "for-each-ref"): FakeProc(
                0,
                stdout="fix/leap-reports-permission-502\nfix/older\n",
            ),
        })
        assert ir.infer_ir_head(tmp_path, runner=runner) == (
            "fix/leap-reports-permission-502"
        )


class TestGrokPromptAndPlaybook:
    def test_autopush_prompt_pushes_fix_branch_and_ensures_pr(self):
        prompt = responder.build_prompt(_page(), autoship=True, autopush=True)
        assert "fix/" in prompt
        assert "ir_ensure_pr.py" in prompt
        assert "Never merge" in prompt or "never merge" in prompt
        assert "Never `git push origin main`" in prompt
        assert "then `git push origin main`" not in prompt

    def test_skill_ships_via_fix_branch_and_ensure_pr(self):
        text = SKILL.read_text(encoding="utf-8")
        assert "fix/" in text
        assert "ir_ensure_pr.py" in text
        assert "never merge" in text.lower()
        assert "Never `git push origin main`" in text
        assert "then `git push origin main`" not in text

    def test_incident_command_does_not_push_main(self):
        text = COMMAND.read_text(encoding="utf-8")
        assert "fix/" in text
        assert "ir_ensure_pr.py" in text

    def test_grok_doc_documents_pat_scopes_and_enablement(self):
        text = GROK_DOC.read_text(encoding="utf-8")
        assert "Contents" in text
        assert "Pull requests" in text
        assert "Administration" not in text or "not" in text.lower()
        assert "ir_ensure_pr.py" in text
        assert "git push origin main" not in text.split("## Path", 1)[1].split(
            "## Install", 1
        )[0]

    def test_runbook_grok_section_opens_a_pr(self):
        text = RUNBOOK.read_text(encoding="utf-8")
        assert "ir_ensure_pr.py" in text
        grok = text.split("Grok auto-response", 1)[1]
        assert "git push origin main" not in grok

    def test_module_and_wrapper_never_merge(self):
        module = (REPO / "scripts" / "ir_ensure_pr.py").read_text(encoding="utf-8")
        wrapper = WRAPPER.read_text(encoding="utf-8")
        assert "pr merge" not in module
        assert "pr merge" not in wrapper
        assert "ir_ensure_pr.py" in wrapper


class TestGrokCycleEnsuresPr:
    def test_code_fix_autopush_calls_ensure_pr(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GROK_PAGE_RESPONDER", "1")
        monkeypatch.setenv("GROK_PAGE_AUTOSHIP", "1")
        monkeypatch.setenv("GROK_PAGE_AUTOPUSH", "1")
        monkeypatch.setattr(
            responder.pages_mod,
            "list_actionable_pages",
            lambda **_: [_page()],
        )
        monkeypatch.setattr(responder.pages_mod, "actions_since", lambda **_: 0)
        monkeypatch.setattr(responder.pages_mod, "claim_page", lambda *a, **k: True)
        completed = []
        monkeypatch.setattr(
            responder.pages_mod,
            "complete_page",
            lambda *a, **k: completed.append(k.get("result") or a),
        )
        monkeypatch.setattr(responder, "_send_followup", lambda **k: None)
        monkeypatch.setattr(responder, "_heartbeat", lambda *a, **k: None)
        monkeypatch.setattr(responder, "sync_remote_clone", lambda _root: "disabled")

        class _Proc:
            returncode = 0
            stdout = "RESULT: code_fix | leap PermissionError"
            stderr = ""

        seen = []

        def ensure(repo_root, *, page, summary):
            seen.append((Path(repo_root), page["page_id"], summary))
            return {
                "action": "created",
                "url": "https://github.com/joemccann/radon/pull/435",
                "head": "fix/leap-reports-permission-502",
            }

        rc = responder.run_cycle(
            tmp_path,
            grok_runner=lambda *a, **k: _Proc(),
            ensure_ir_pr=ensure,
        )
        assert rc == 0
        assert seen
        assert seen[0][1] == "page-leap"
        assert any(
            "https://github.com/joemccann/radon/pull/435" in str(item)
            for item in completed
        )

    def test_pr_failure_fails_the_cycle(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GROK_PAGE_RESPONDER", "1")
        monkeypatch.setenv("GROK_PAGE_AUTOSHIP", "1")
        monkeypatch.setenv("GROK_PAGE_AUTOPUSH", "1")
        monkeypatch.setattr(
            responder.pages_mod,
            "list_actionable_pages",
            lambda **_: [_page()],
        )
        monkeypatch.setattr(responder.pages_mod, "actions_since", lambda **_: 0)
        monkeypatch.setattr(responder.pages_mod, "claim_page", lambda *a, **k: True)
        monkeypatch.setattr(responder.pages_mod, "complete_page", lambda *a, **k: None)
        monkeypatch.setattr(responder, "_send_followup", lambda **k: None)
        monkeypatch.setattr(responder, "_heartbeat", lambda *a, **k: None)
        monkeypatch.setattr(responder, "sync_remote_clone", lambda _root: "disabled")

        class _Proc:
            returncode = 0
            stdout = "RESULT: code_fix | leap PermissionError"
            stderr = ""

        def ensure(repo_root, *, page, summary):
            raise ir.IrEnsurePrError(ir.PAT_SCOPE_ERROR)

        rc = responder.run_cycle(
            tmp_path,
            grok_runner=lambda *a, **k: _Proc(),
            ensure_ir_pr=ensure,
        )
        assert rc == 2

    def test_stand_down_does_not_open_a_pr(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GROK_PAGE_RESPONDER", "1")
        monkeypatch.setenv("GROK_PAGE_AUTOSHIP", "1")
        monkeypatch.setenv("GROK_PAGE_AUTOPUSH", "1")
        monkeypatch.setattr(
            responder.pages_mod,
            "list_actionable_pages",
            lambda **_: [_page()],
        )
        monkeypatch.setattr(responder.pages_mod, "actions_since", lambda **_: 0)
        monkeypatch.setattr(responder.pages_mod, "claim_page", lambda *a, **k: True)
        monkeypatch.setattr(responder.pages_mod, "complete_page", lambda *a, **k: None)
        monkeypatch.setattr(responder, "_send_followup", lambda **k: None)
        monkeypatch.setattr(responder, "_heartbeat", lambda *a, **k: None)
        monkeypatch.setattr(responder, "sync_remote_clone", lambda _root: "disabled")

        class _Proc:
            returncode = 0
            stdout = "RESULT: stand_down | expected off-hours lag"
            stderr = ""

        def ensure(*_a, **_k):
            raise AssertionError("stand_down must not open a PR")

        rc = responder.run_cycle(
            tmp_path,
            grok_runner=lambda *a, **k: _Proc(),
            ensure_ir_pr=ensure,
        )
        assert rc == 0


class TestCliAndWrapper:
    def test_cli_emits_json(self, monkeypatch, capsys):
        monkeypatch.setattr(
            ir,
            "ensure_pr",
            lambda **k: {
                "action": "exists",
                "url": "https://github.com/joemccann/radon/pull/1",
                "head": "fix/example",
            },
        )
        rc = ir.main([
            "--head", "fix/example",
            "--issue", "A.",
            "--fix", "B.",
            "--json",
        ])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["action"] == "exists"

    def test_wrapper_is_executable_and_delegates(self):
        assert WRAPPER.is_file()
        text = WRAPPER.read_text(encoding="utf-8")
        assert "ir_ensure_pr.py" in text
        assert subprocess.run(
            ["bash", "-n", str(WRAPPER)], check=False
        ).returncode == 0
