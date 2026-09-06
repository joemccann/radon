"""The provider block is copied into five wrappers; only a test stops drift.

Each loop is a standalone script by design — a shared library would put one
file in the blast radius of all five nightly runs. The cost of that choice is
five copies of the same eleven functions, and the only thing keeping them
identical is this file.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

_H = Path(__file__).with_name("_loop_harness.py")
_spec = importlib.util.spec_from_file_location("_loop_harness_prp", _H)
_h = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _h
_spec.loader.exec_module(_h)

LOOPS = _h.LOOPS

SHARED = [
    "rung_provider",
    "rung_model",
    "provider_bin",
    "provider_key_present",
    "provider_ready",
    "load_provider_key",
    "use_rung",
    "advance_rung",
    "quota_regex",
    "session_regex",
    "launch_round",
]


def _fn(text: str, name: str) -> str:
    """Both shapes: a one-line `f() { ...; }` and a multi-line body."""
    esc = re.escape(name)
    m = re.search(rf"^{esc}\(\) \{{[^\n]*\}}$", text, re.M)
    if not m:
        m = re.search(rf"^{esc}\(\) \{{\n(?:.*\n)*?^\}}$", text, re.M)
    assert m, f"{name}() not found"
    return m.group(0)


@pytest.mark.parametrize("fn", SHARED)
def test_the_helper_is_byte_identical_across_the_five_wrappers(fn):
    bodies = {n: _fn(p.read_text(encoding="utf-8"), fn) for n, p in LOOPS.items()}
    assert len(set(bodies.values())) == 1, (
        f"{fn}() has drifted between loops: "
        f"{sorted(n for n in bodies)} produced {len(set(bodies.values()))} variants"
    )


@pytest.mark.parametrize("loop", sorted(LOOPS))
def test_each_loop_declares_its_own_skill_and_slug(loop):
    body = LOOPS[loop].read_text(encoding="utf-8")
    assert re.search(r'^LOOP_SKILL="[a-z-]+"$', body, re.M), "no LOOP_SKILL"
    assert re.search(r'^LOOP_SLUG="[a-z-]+"$', body, re.M), "no LOOP_SLUG"


@pytest.mark.parametrize("loop", sorted(LOOPS))
def test_begin_phase_resets_exhaustion_but_keeps_the_rung(loop):
    """REL-198 (R-533): rung carry across phases is intended — a provider that
    is capped at 00:00 is still capped at 02:00 — but flag carry is not."""
    body = "\n".join(
        ln for ln in LOOPS[loop].read_text(encoding="utf-8").splitlines()
        if not ln.lstrip().startswith("#")
    )
    fn = body[body.index("begin_phase()") : body.index("\n}", body.index("begin_phase()"))]
    assert "ALL_PROVIDERS_EXHAUSTED=0" in fn
    assert "EXHAUSTED_PROVIDERS=" in fn
    assert "RUNG_INDEX" not in fn, "rung carry must be preserved"


def test_the_four_fallback_loops_lead_with_codex_and_never_name_claude():
    for loop in ("reliability", "testing", "documentation", "ci-performance"):
        body = LOOPS[loop].read_text(encoding="utf-8")
        m = re.search(r'^PROVIDER_LADDER="\$\{RADON_WEEKEND_PROVIDER_LADDER:-(.+?)\}"$',
                      body, re.M)
        assert m, f"{loop}: no default provider ladder"
        rungs = m.group(1).split()
        assert [r.split(":")[0] for r in rungs] == [
            "codex", "grok", "nvidia", "cerebras"
        ], (loop, rungs)
        assert not any(r.startswith("claude:") for r in rungs), (
            f"{loop}: the claude.ai subscription is reserved for the security "
            f"loop: {rungs}"
        )


def test_the_security_loop_ladder_is_claude_only():
    body = LOOPS["security"].read_text(encoding="utf-8")
    assert "refuse_non_claude_rung" in body
    assert "claude-exclusive" in body
    m = re.search(r'^MODEL_LADDER="\$\{RADON_WEEKEND_MODEL_LADDER:-(.+?)\}"$', body, re.M)
    assert m, "the security loop lost its claude model ladder"
    assert m.group(1).split() == _h.LADDER, m.group(1)
