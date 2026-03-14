"""Tests for the MenthorQ slash command skill structure.

Validates:
- SKILL.md exists with correct frontmatter and required sections
- commands.json includes all menthorq commands
- docs/menthorq-prompts.md is referenced and contains expected categories
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SKILL_DIR = PROJECT_ROOT / ".pi" / "skills" / "menthorq"
SKILL_MD = SKILL_DIR / "SKILL.md"
COMMANDS_JSON = PROJECT_ROOT / ".pi" / "commands.json"
PROMPTS_MD = PROJECT_ROOT / "docs" / "menthorq-prompts.md"


# ── SKILL.md existence & frontmatter ────────────────────────────────

class TestSkillMdExists:
    def test_skill_dir_exists(self):
        assert SKILL_DIR.is_dir(), f"Missing skill directory: {SKILL_DIR}"

    def test_skill_md_exists(self):
        assert SKILL_MD.is_file(), f"Missing SKILL.md: {SKILL_MD}"

    def test_frontmatter_has_name(self):
        content = SKILL_MD.read_text()
        assert re.search(r"^name:\s*menthorq", content, re.MULTILINE), \
            "SKILL.md frontmatter missing 'name: menthorq'"

    def test_frontmatter_has_description(self):
        content = SKILL_MD.read_text()
        assert re.search(r"^description:", content, re.MULTILINE), \
            "SKILL.md frontmatter missing 'description:'"


# ── SKILL.md required sections ──────────────────────────────────────

class TestSkillMdSections:
    @pytest.fixture
    def content(self):
        return SKILL_MD.read_text()

    def test_has_commands_section(self, content):
        assert "## Commands" in content or "## Available Commands" in content, \
            "SKILL.md must document available commands"

    def test_documents_menthorq_cta(self, content):
        assert "menthorq-cta" in content, \
            "SKILL.md must document the menthorq-cta command"

    def test_documents_menthorq_dashboard(self, content):
        assert "menthorq-dashboard" in content, \
            "SKILL.md must document the menthorq-dashboard command"

    def test_documents_menthorq_screener(self, content):
        assert "menthorq-screener" in content, \
            "SKILL.md must document the menthorq-screener command"

    def test_documents_menthorq_forex(self, content):
        assert "menthorq-forex" in content, \
            "SKILL.md must document the menthorq-forex command"

    def test_documents_menthorq_summary(self, content):
        assert "menthorq-summary" in content, \
            "SKILL.md must document the menthorq-summary command"

    def test_references_prompts_file(self, content):
        assert "menthorq-prompts.md" in content, \
            "SKILL.md must reference docs/menthorq-prompts.md"

    def test_has_quin_screener_section(self, content):
        assert "QUIN" in content, \
            "SKILL.md must document the QUIN AI screener prompts"

    def test_has_preset_prompts(self, content):
        """SKILL.md must include at least 5 preset prompt examples."""
        # Count lines inside code blocks that look like prompts
        prompt_lines = re.findall(
            r"^(?:Top |Show |Compare |Stocks |ETFs |Tickers |Which )",
            content,
            re.MULTILINE,
        )
        assert len(prompt_lines) >= 5, \
            f"Expected ≥5 preset prompts, found {len(prompt_lines)}"


# ── commands.json registration ──────────────────────────────────────

class TestCommandsJson:
    @pytest.fixture
    def commands(self):
        data = json.loads(COMMANDS_JSON.read_text())
        return {c["command"] for c in data["commands"]}

    def test_menthorq_cta_registered(self, commands):
        assert "menthorq-cta" in commands

    def test_menthorq_dashboard_registered(self, commands):
        matching = [c for c in commands if c.startswith("menthorq-dashboard")]
        assert len(matching) >= 1, "menthorq-dashboard command not registered"

    def test_menthorq_screener_registered(self, commands):
        matching = [c for c in commands if c.startswith("menthorq-screener")]
        assert len(matching) >= 1, "menthorq-screener command not registered"

    def test_menthorq_forex_registered(self, commands):
        assert "menthorq-forex" in commands

    def test_menthorq_summary_registered(self, commands):
        matching = [c for c in commands if c.startswith("menthorq-summary")]
        assert len(matching) >= 1, "menthorq-summary command not registered"

    def test_menthorq_quin_registered(self, commands):
        """The new menthorq-quin command must be registered."""
        matching = [c for c in commands if c.startswith("menthorq-quin")]
        assert len(matching) >= 1, "menthorq-quin command not registered"


# ── Prompts doc structure ───────────────────────────────────────────

class TestPromptsDoc:
    @pytest.fixture
    def content(self):
        return PROMPTS_MD.read_text()

    def test_prompts_file_exists(self):
        assert PROMPTS_MD.is_file(), f"Missing prompts doc: {PROMPTS_MD}"

    def test_has_screening_category(self, content):
        assert "Screening & Rankings" in content

    def test_has_comparisons_category(self, content):
        assert "Multi-Ticker Comparisons" in content

    def test_has_historical_category(self, content):
        assert "Historical Data & Trends" in content

    def test_has_percentiles_category(self, content):
        assert "Percentiles" in content

    def test_has_available_metrics(self, content):
        assert "Available Metrics" in content

    def test_has_at_least_8_categories(self, content):
        """Must have all 8 prompt categories."""
        headers = re.findall(r"^### \d+\.", content, re.MULTILINE)
        assert len(headers) >= 8, f"Expected ≥8 categories, found {len(headers)}"

    def test_has_at_least_30_prompts(self, content):
        """Must have at least 30 example prompts in code blocks."""
        # Count non-empty lines inside ``` blocks
        in_block = False
        prompt_count = 0
        for line in content.splitlines():
            if line.strip().startswith("```"):
                in_block = not in_block
                continue
            if in_block and line.strip():
                prompt_count += 1
        assert prompt_count >= 30, f"Expected ≥30 prompts, found {prompt_count}"
