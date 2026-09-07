"""Each provider is launched with the argv it actually needs.

A rung that launches is not a rung that works. The likeliest silent failure
here is handing a non-Claude CLI the string `/testing-weekend audit`: codex
and grok cannot resolve a Claude slash command, so they would burn the cap
doing nothing and exit 0 — a night that looks green and audited nothing.
These assert the wire, not the intent.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

_H = Path(__file__).with_name("_loop_harness.py")
_spec = importlib.util.spec_from_file_location("_loop_harness_pic", _H)
_h = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _h
_spec.loader.exec_module(_h)

LOOPS = _h.LOOPS
FALLBACK_LOOPS = ["ci-performance", "documentation", "reliability", "testing"]


def _argv(tmp_path, loop, phase="audit", **kw):
    _proc, _tried, _calls, argv = _h._run_multi(tmp_path, loop, phase, **kw)
    return argv


@pytest.mark.parametrize("loop", FALLBACK_LOOPS)
class TestTheWire:
    def test_codex_runs_exec_with_the_prompt_on_stdin(self, tmp_path, loop):
        argv = _argv(tmp_path, loop)
        assert argv, "codex was never launched"
        first = argv[0]
        assert first.startswith("exec "), first
        assert "--model" not in first.split(), (
            f"the codex rung must not pin a model: {first}"
        )
        assert "model_reasoning_effort" in first, first
        assert "--sandbox workspace-write" in first, (
            "codex must get the same bounded grant the claude rung has, not "
            f"--dangerously-bypass-approvals-and-sandbox: {first}"
        )
        assert "--skip-git-repo-check" in first, first

    def test_codex_never_receives_a_slash_command(self, tmp_path, loop):
        argv = _argv(tmp_path, loop)
        assert not re.search(r"/\w+-\w+ (audit|remediate|deliver)", argv[0]), (
            f"a Claude slash command reached codex, which cannot resolve it: {argv[0]}"
        )

    def test_grok_gets_a_prompt_file_and_the_repo_cwd(self, tmp_path, loop):
        argv = _argv(tmp_path, loop, capped_providers=("codex",))
        assert len(argv) >= 2, argv
        grok = argv[1]
        assert "--prompt-file" in grok, grok
        assert "--model" not in grok.split(), (
            f"the grok rung must not pin a model: {grok}"
        )
        assert "--reasoning-effort medium" in grok, grok
        assert "--cwd" in grok, grok
        assert "--output-format plain" in grok, grok

    def test_the_prompt_file_names_this_loop_and_phase(self, tmp_path, loop):
        skill = {
            "ci-performance": "ci-performance",
            "documentation": "documentation-nightly",
            "reliability": "reliability-weekend",
            "testing": "testing-weekend",
        }[loop]
        for phase in ("audit", "remediate", "deliver"):
            sub = tmp_path / phase
            sub.mkdir(parents=True, exist_ok=True)
            argv = _argv(sub, loop, phase, capped_providers=("codex",))
            assert f"{skill}.{phase}.md" in argv[1], (phase, argv[1])


@pytest.mark.parametrize("loop", sorted(LOOPS))
class TestTheWrapperSource:
    def test_no_absolute_provider_path_without_an_env_override(self, loop):
        """A hardcoded /opt/homebrew path with no override is untestable and
        unfixable on a host that puts the binary elsewhere."""
        body = LOOPS[loop].read_text(encoding="utf-8")
        for hard, override in (
            ("/opt/homebrew/bin/codex", "RADON_WEEKEND_CODEX_BIN"),
            ("$HOME/.grok/bin/grok", "RADON_WEEKEND_GROK_BIN"),
        ):
            if hard in body:
                assert override in body, f"{hard} has no {override} override"

    def test_codex_is_not_resolved_through_path(self, loop):
        """`command -v codex` finds the npm shim, whose vendor directory on
        this host is empty; the binary it execs does not exist."""
        body = LOOPS[loop].read_text(encoding="utf-8")
        assert "command -v codex" not in body

    def test_every_provider_launch_is_wrapped_in_timeout(self, loop):
        body = LOOPS[loop].read_text(encoding="utf-8")
        start = body.index("launch_round() {")
        fn = body[start : body.index("\n}", start)]
        launches = [ln for ln in fn.splitlines() if ln.rstrip().endswith("&")]
        assert launches, "launch_round starts nothing"
        assert fn.count('"$TIMEOUT_BIN" -k "$KILL_AFTER_SECS"') >= 4, (
            "every provider arm must go through timeout -k, or the cap is "
            f"advisory for that provider:\n{fn}"
        )

    def test_the_rung_travels_as_environment_too(self, loop):
        """A --model flag binds one process; the security skill spawns a
        nested claude that reads the environment instead. DOC-042."""
        body = LOOPS[loop].read_text(encoding="utf-8")
        assert "export RADON_WEEKEND_MODEL=" in body
        assert "export RADON_WEEKEND_PROVIDER=" in body


@pytest.mark.parametrize("loop", sorted(LOOPS))
class TestTheAgentCanReachGit:
    """Every phase is scored on a COMMIT to the dated branch.

    2026-09-07: codex's own workspace-write policy protects version-control
    metadata, so every codex phase died with

      fatal: cannot lock ref 'refs/heads/<loop>/<date>': Unable to create
      '.../.git/refs/heads/....lock': Operation not permitted

    and then scored INCOMPLETE for having no commit — silently, on all four
    fallback loops, every run. A rung that cannot write .git can never satisfy
    the contract, so the grant is asserted here rather than discovered at 00:00.
    """

    def test_codex_names_the_clone_git_dir_as_a_writable_root(self, loop):
        body = LOOPS[loop].read_text(encoding="utf-8")
        start = body.index("launch_round() {")
        fn = body[start : body.index("\n}", start)]
        assert "sandbox_workspace_write" in fn, (
            "codex cannot create a branch under the default workspace-write "
            "policy, so the phase can never commit and is scored INCOMPLETE"
        )
        assert 'writable_roots=[\\"$REPO/.git\\"]' in fn or "$REPO/.git" in fn, fn

    def test_the_grant_is_scoped_and_the_bypass_flag_stays_off(self, loop):
        """Widened to this clone's git directory, not to the whole machine."""
        body = LOOPS[loop].read_text(encoding="utf-8")
        assert "--dangerously-bypass-approvals-and-sandbox" not in body, (
            "the codex rung must keep a bounded grant, matching the claude "
            "rung's --dangerously-skip-permissions scope, not exceed it"
        )
        assert "--sandbox workspace-write" in body
