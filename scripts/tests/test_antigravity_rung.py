"""The Antigravity CLI (`agy`) is a rung on the four fallback loops' ladder.

2026-09-27: a documentation cycle led by fx:nvidia logged 232 HTTP 429s in a
17-minute audit and 124 more before remediate's tool-loop guard pushed it to
grok, which then finished with none. The ladder now leads on grok and codex,
and Antigravity sits ahead of NVIDIA.

agy print mode takes the prompt only when attached to the flag (`-p=<text>`):
a detached `-p` swallows the next flag as the prompt and exits (agy 1.2.12).
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

_H = Path(__file__).with_name("_loop_harness.py")
_spec = importlib.util.spec_from_file_location("_loop_harness_agy", _H)
_h = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _h
_spec.loader.exec_module(_h)

FALLBACK = sorted(_h.FALLBACK_PROVIDER_ORDER)
LADDER = ["grok", "codex", "antigravity", "fx:nvidia", "fx:cerebras"]


@pytest.mark.parametrize("loop", FALLBACK)
def test_the_default_ladder_leads_on_grok_and_puts_antigravity_ahead_of_nvidia(loop):
    text = _h.LOOPS[loop].read_text(encoding="utf-8")
    m = re.search(r'^PROVIDER_LADDER="\$\{RADON_WEEKEND_PROVIDER_LADDER:-([^}]*)\}"', text, re.M)
    assert m and m.group(1).split() == LADDER, m and m.group(1)


@pytest.mark.parametrize("loop", FALLBACK)
def test_agy_launches_with_the_prompt_attached_in_the_clone(tmp_path, loop):
    rung, argv, _tried = _h._launch_of(tmp_path, loop, "antigravity")
    assert rung == "antigravity:", rung
    args = argv.split()
    assert args[0] == "-p=stub", argv
    assert "--dangerously-skip-permissions" in args, argv
    assert args[args.index("--effort") + 1] == "medium", argv
    assert "--output-format" in args and args[args.index("--output-format") + 1] == "text", argv
    pwd = (tmp_path / "attempts.tsv.agy-pwd").read_text(encoding="utf-8").strip()
    assert pwd.startswith("PWD=") and pwd.endswith("/clone"), pwd


@pytest.mark.parametrize("loop", FALLBACK)
def test_a_resource_exhausted_agy_drops_to_nvidia(tmp_path, loop):
    _proc, tried, _calls, _argv = _h._run_multi(
        tmp_path, loop, "audit", provider_ladder="antigravity fx:nvidia",
        capped_providers=("antigravity",),
    )
    assert [t.rstrip(":") for t in tried] == ["antigravity", "fx:nvidia"], tried


@pytest.mark.parametrize("loop", FALLBACK)
def test_a_signed_out_agy_costs_one_rung(tmp_path, loop):
    _proc, tried, _calls, _argv = _h._run_multi(
        tmp_path, loop, "audit", provider_ladder="antigravity fx:nvidia",
        reject_providers=("antigravity",), reject_output="Error: authentication required",
    )
    assert [t.rstrip(":") for t in tried] == ["antigravity", "fx:nvidia"], tried


@pytest.mark.parametrize("loop", FALLBACK)
def test_an_uninstalled_agy_is_skipped(tmp_path, loop):
    _proc, tried, _calls, _argv = _h._run_multi(
        tmp_path, loop, "audit", provider_ladder="antigravity fx:nvidia",
        installed=("claude", "codex", "grok", "fx"),
    )
    assert [t.rstrip(":") for t in tried] == ["fx:nvidia"], tried
