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
    "rejection_regex",
    "launch_round",
]
# launch_round is identical within each family. Security + DeepSec pin
# `--effort medium` on the claude arm (2026-09-21 Fable-limit night);
# the four fallback loops do not take that arm and stay on their copy.
IDENTICAL_ACROSS_ALL = [n for n in SHARED if n != "launch_round"]
SECURITY_LOOPS = ("security", "security-deepsec")
FALLBACK_LOOPS = ("reliability", "testing", "documentation", "ci-performance")


def _fn(text: str, name: str) -> str:
    """Both shapes: a one-line `f() { ...; }` and a multi-line body."""
    esc = re.escape(name)
    m = re.search(rf"^{esc}\(\) \{{[^\n]*\}}$", text, re.M)
    if not m:
        m = re.search(rf"^{esc}\(\) \{{\n(?:.*\n)*?^\}}$", text, re.M)
    assert m, f"{name}() not found"
    return m.group(0)


@pytest.mark.parametrize("fn", IDENTICAL_ACROSS_ALL)
def test_the_helper_is_byte_identical_across_the_five_wrappers(fn):
    bodies = {n: _fn(p.read_text(encoding="utf-8"), fn) for n, p in LOOPS.items()}
    assert len(set(bodies.values())) == 1, (
        f"{fn}() has drifted between loops: "
        f"{sorted(n for n in bodies)} produced {len(set(bodies.values()))} variants"
    )


def test_launch_round_is_identical_within_each_ladder_family():
    bodies = {n: _fn(p.read_text(encoding="utf-8"), "launch_round") for n, p in LOOPS.items()}
    security = {bodies[n] for n in SECURITY_LOOPS}
    fallback = {bodies[n] for n in FALLBACK_LOOPS}
    assert len(security) == 1, "security launch_round drifted between wrappers"
    assert len(fallback) == 1, "fallback launch_round drifted between wrappers"


@pytest.mark.parametrize("loop", SECURITY_LOOPS)
def test_security_claude_arm_passes_effort_medium(loop):
    body = LOOPS[loop].read_text(encoding="utf-8")
    arm_start = body.index("    claude)\n", body.index("launch_round() {"))
    arm = body[arm_start:body.index(";;", arm_start)]
    assert "--effort medium" in arm, arm


@pytest.mark.parametrize("loop", sorted(LOOPS))
def test_each_loop_declares_its_own_skill_and_slug(loop):
    body = LOOPS[loop].read_text(encoding="utf-8")
    assert re.search(r'^LOOP_SKILL="[a-z-]+"$', body, re.M), "no LOOP_SKILL"
    assert re.search(r'^LOOP_LOG_TAG="[a-z-]+"$', body, re.M), "no LOOP_LOG_TAG"


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


@pytest.mark.parametrize("loop", SECURITY_LOOPS)
def test_the_security_loop_ladder_is_claude_only(loop):
    body = LOOPS[loop].read_text(encoding="utf-8")
    assert "refuse_non_claude_rung" in body
    assert "claude-exclusive" in body
    assert ". \"$REPO/scripts/security_claude_ladder.sh\"" in body, (
        f"{loop}: must source the shared skip-newest helper"
    )
    assert not re.search(
        r'^MODEL_LADDER="\$\{RADON_WEEKEND_MODEL_LADDER:-claude-',
        body,
        re.M,
    ), f"{loop}: static MODEL_LADDER pin leaked back into the wrapper"
