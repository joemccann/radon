"""The MenthorQ prompt catalogue (docs/menthorq-prompts.md) keeps its categories and prompts.

The Pi-harness MenthorQ skill and command registry were deleted on 2026-11-11.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
PROMPTS_MD = PROJECT_ROOT / "docs" / "menthorq-prompts.md"


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
