"""The upgrade smoke validates the IR body inside grok's JSON output."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import grok_upgrade as upgrade  # noqa: E402
import ir_pr_description  # noqa: E402
from grok_page_responder import grok_output_text, parse_grok_result  # noqa: E402

RESULT_LINE = "RESULT: stand_down | grok upgrade smoke returned a structured IR summary"

BODY = """## What broke
Nothing broke during this invented dry-run. The scratch directory stayed empty and untouched.

## Root cause
The check only asks the candidate to write a summary. No product defect was in scope for it.

## What changed
No file was written and no remote was updated. The only output is this six-section summary.

## How it was verified
The reply follows the requested headings in order. Each section holds two plain sentences.

## Risk and rollback
Risk is low because nothing was installed or edited here. Rollback means keeping the current binary.

## Still open
Promotion still depends on the upgrader accepting this body. Nothing else is pending for the smoke.

""" + RESULT_LINE


def _grok_json(text: str, *, num_turns: int = 1) -> str:
    """Same shape as `grok --output-format json` on CLI 1.0.44 (invented values)."""
    return json.dumps(
        {
            "text": text,
            "stopReason": "end_turn",
            "sessionId": "00000000-0000-0000-0000-000000000000",
            "requestId": "11111111-1111-1111-1111-111111111111",
            "thought": "Planning a short dry-run summary.",
            "usage": {"input_tokens": 1000, "output_tokens": 200},
            "num_turns": num_turns,
            "total_cost_usd": 0.01,
        }
    )


# A tool call between two assistant messages: grok joins the messages with no
# separator, so the preamble and the first heading share one line.
GLUED = "This is a read-only dry run. I'll check the workspace, then write the summary." + BODY
# A preamble on its own line before the first heading.
PREAMBLE = "I'll inspect the workspace before writing the summary.\n\n" + BODY


def _run(stdout: str, tmp_path: Path):
    runner = lambda argv, **kw: SimpleNamespace(returncode=0, stdout=stdout, stderr="")  # noqa: E731
    return upgrade.run_smoke(
        grok_bin="grok",
        model="grok-test",
        reasoning_effort="high",
        scratch=tmp_path / "scratch",
        runner=runner,
    )


class TestGrokOutputText:
    def test_json_object_yields_text(self):
        assert grok_output_text(_grok_json(BODY)) == BODY

    def test_plain_text_passes_through(self):
        assert grok_output_text(BODY) == BODY

    def test_json_without_text_falls_back_to_raw(self):
        raw = json.dumps({"stopReason": "error"})
        assert grok_output_text(raw) == raw

    def test_parse_grok_result_uses_the_same_text(self):
        assert parse_grok_result(_grok_json(GLUED)) == (
            "stand_down",
            "grok upgrade smoke returned a structured IR summary",
        )


class TestRunSmoke:
    @pytest.mark.parametrize("text", [BODY, PREAMBLE, GLUED], ids=["clean", "preamble", "glued"])
    def test_json_output_passes(self, tmp_path, text):
        disposition, summary = _run(_grok_json(text, num_turns=2), tmp_path)
        assert disposition == "stand_down"
        assert summary == "grok upgrade smoke returned a structured IR summary"

    def test_plain_text_output_still_passes(self, tmp_path):
        assert _run(BODY, tmp_path)[0] == "stand_down"

    def test_missing_section_still_fails(self, tmp_path):
        text = BODY.replace("## Root cause\n", "")
        with pytest.raises(ir_pr_description.IrDescriptionError, match="Root cause"):
            _run(_grok_json(text), tmp_path)

    def test_no_heading_at_all_still_fails(self, tmp_path):
        text = "I could not finish the summary.\n" + RESULT_LINE
        with pytest.raises(ir_pr_description.IrDescriptionError, match="What broke"):
            _run(_grok_json(text), tmp_path)

    def test_prompt_forbids_preamble(self):
        assert "first line must be `## What broke`" in upgrade.SMOKE_PROMPT


class TestSmokeBody:
    def test_cuts_the_glued_preamble(self):
        assert upgrade.smoke_ir_body(GLUED).startswith("## What broke\n")

    def test_inline_heading_is_not_a_heading_for_the_validator(self):
        with pytest.raises(ir_pr_description.IrDescriptionError, match="What broke"):
            ir_pr_description.validate_ir_description(GLUED)

    def test_preamble_line_is_ignored_by_the_validator(self):
        ir_pr_description.validate_ir_description(PREAMBLE)
