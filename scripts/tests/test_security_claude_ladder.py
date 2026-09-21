"""Security / DeepSec Claude ladder: skip newest, not a static opus pin.

Policy (Joe 2026-09-21): Mini `claude models` (subscription CLI only),
newest-first, collapse aliases of one generation, drop index 0, run the
prior model then deeper Claude fallbacks. Override env short-circuits.
Discovery failure uses SAFETY_LADDER and logs. Tests fixture the catalog;
they must not hit the network.
"""

from __future__ import annotations

import importlib.util
import os
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
HELPER_PY = REPO / "scripts" / "security_claude_ladder.py"
HELPER_SH = REPO / "scripts" / "security_claude_ladder.sh"
SECURITY = REPO / "scripts" / "security_nightly.sh"
DEEPSEC = REPO / "scripts" / "security_deepsec_nightly.sh"

MINI_20260921 = """
Available models:
  claude-fable-5-1
  claude-opus-5
  claude-sonnet-5
  claude-haiku-4-5
"""

ALIASED = """
claude-fable-5-1
claude-fable-5[1m]
claude-opus-5
claude-opus-5[1m]
claude-sonnet-5
"""


def _load():
    spec = importlib.util.spec_from_file_location("security_claude_ladder", HELPER_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def ladder():
    return _load()


class TestCatalogParsing:
    def test_newest_first_text_skips_index_zero(self, ladder):
        got = ladder.ladder_from_text(MINI_20260921)
        assert got[0] == "claude-opus-5"
        assert "claude-fable-5-1" not in got
        assert got == ["claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5"]

    def test_aliases_of_one_generation_collapse(self, ladder):
        got = ladder.ladder_from_text(ALIASED)
        assert got == ["claude-opus-5", "claude-sonnet-5"]
        assert got[0] != "claude-fable-5-1"
        assert "claude-opus-5[1m]" not in got

    def test_json_catalog_is_newest_first(self, ladder):
        body = (
            '{"data":[{"id":"claude-fable-5-1"},'
            '{"id":"claude-opus-5"},{"id":"claude-sonnet-5"}]}'
        )
        assert ladder.ladder_from_text(body) == ["claude-opus-5", "claude-sonnet-5"]

    def test_fable_and_fable_point_one_are_one_generation(self, ladder):
        assert ladder.same_family("claude-fable-5", "claude-fable-5-1")
        assert ladder.same_family("claude-opus-5[1m]", "claude-opus-5")
        assert not ladder.same_family("claude-opus-5", "claude-opus-4")

    def test_one_generation_is_not_a_ladder(self, ladder):
        assert ladder.ladder_from_text("claude-opus-5\nclaude-opus-5[1m]\n") == []

    def test_empty_text_is_not_a_ladder(self, ladder):
        assert ladder.ladder_from_text("") == []
        assert ladder.ladder_from_text("no models here") == []


class TestSafetyAndNoNetwork:
    def test_safety_ladder_excludes_fable_and_is_not_the_policy_pin(self, ladder):
        assert ladder.SAFETY_LADDER == ("claude-opus-5", "claude-sonnet-5")
        assert not any("fable" in m for m in ladder.SAFETY_LADDER)

    def test_module_never_names_the_anthropic_api_host(self):
        src = HELPER_PY.read_text(encoding="utf-8")
        assert "https://" not in src
        assert "refresh_model_catalog" not in src
        assert "DEFAULT_MODELS_CMD" in src
        assert 'claude models' in src

    def test_from_text_does_not_spawn_claude(self, ladder, monkeypatch, tmp_path):
        def boom(*_a, **_k):
            raise AssertionError("catalog fixture must not spawn claude")

        monkeypatch.setattr(ladder.subprocess, "run", boom)
        fixture = tmp_path / "catalog.txt"
        fixture.write_text(MINI_20260921, encoding="utf-8")
        assert ladder.main(["--from-text", str(fixture)]) == 0

    def test_catalog_env_does_not_spawn_claude(self, ladder, monkeypatch, tmp_path):
        def boom(*_a, **_k):
            raise AssertionError("RADON_WEEKEND_CLAUDE_CATALOG must not spawn claude")

        monkeypatch.setattr(ladder.subprocess, "run", boom)
        fixture = tmp_path / "catalog.txt"
        fixture.write_text(MINI_20260921, encoding="utf-8")
        monkeypatch.setenv("RADON_WEEKEND_CLAUDE_CATALOG", str(fixture))
        assert ladder.resolve_ladder() == ["claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5"]

    def test_scrubbed_env_drops_billing_keys(self, ladder, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-secret")
        monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok")
        env = ladder._scrubbed_env()
        assert "ANTHROPIC_API_KEY" not in env
        assert "ANTHROPIC_AUTH_TOKEN" not in env


class TestCliMain:
    def test_stdout_is_the_ladder_stderr_names_the_skip(self, ladder, tmp_path, capsys):
        fixture = tmp_path / "catalog.txt"
        fixture.write_text(MINI_20260921, encoding="utf-8")
        assert ladder.main(["--from-text", str(fixture)]) == 0
        out, err = capsys.readouterr()
        assert out.strip() == "claude-opus-5 claude-sonnet-5 claude-haiku-4-5"
        assert "skip-newest claude-fable-5-1" in err

    def test_empty_catalog_exits_1_with_no_stdout_ladder(self, ladder, tmp_path, capsys):
        fixture = tmp_path / "catalog.txt"
        fixture.write_text("nothing\n", encoding="utf-8")
        assert ladder.main(["--from-text", str(fixture)]) == 1
        out, err = capsys.readouterr()
        assert out.strip() == ""
        assert "no skip-newest ladder" in err


class TestWrappersShareTheHelper:
    def test_both_nightlies_source_the_same_helper(self):
        for wrapper in (SECURITY, DEEPSEC):
            body = wrapper.read_text(encoding="utf-8")
            assert 'security_claude_ladder.sh' in body, wrapper.name
            assert re.search(
                r'\. "\$REPO/scripts/security_claude_ladder\.sh"', body
            ), wrapper.name
            # Policy lives in the helper, not a forever-hardcoded wrapper pin.
            assert not re.search(
                r'^MODEL_LADDER="\$\{RADON_WEEKEND_MODEL_LADDER:-claude-opus-5',
                body,
                re.M,
            ), wrapper.name

    def test_helper_shell_honors_override_env_and_safety(self):
        sh = HELPER_SH.read_text(encoding="utf-8")
        assert "RADON_WEEKEND_MODEL_LADDER" in sh
        assert "RADON_WEEKEND_PROVIDER_LADDER" in sh
        assert "claude-opus-5 claude-sonnet-5" in sh
        assert "newest/fable excluded" in sh
        assert "security_claude_ladder.py" in sh

    def test_claude_arm_still_pins_effort_medium(self):
        for wrapper in (SECURITY, DEEPSEC):
            body = wrapper.read_text(encoding="utf-8")
            arm_start = body.index("    claude)\n", body.index("launch_round() {"))
            arm = body[arm_start:body.index(";;", arm_start)]
            assert "--effort medium" in arm, wrapper.name
            assert "fable" not in arm.lower()
