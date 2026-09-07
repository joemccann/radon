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
        assert "--model gpt-5.4" in first, first
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
        assert "--model grok-4.6" in grok, grok
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
