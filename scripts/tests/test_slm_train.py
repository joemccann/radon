"""HR-2 / HR-5 train config pins and train.sh refusals."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from newsfeed.slm.contract import SLM_BASE_ID

REPO = Path(__file__).resolve().parents[2]
YAML = REPO / "scripts" / "newsfeed" / "slm" / "configs" / "qwen25-1p5b-qlora-v1.yaml"
TRAIN = REPO / "scripts" / "newsfeed" / "slm" / "train.sh"


class TestConfig:
    def test_base_is_qwen_1p5b(self):
        text = YAML.read_text(encoding="utf-8")
        assert SLM_BASE_ID == "Qwen/Qwen2.5-1.5B-Instruct"
        assert SLM_BASE_ID in text
        assert "mask_prompt: true" in text
        assert "fine_tune_type: lora" in text
        assert "q-bits 4" in text

    def test_card_and_placeholder_manifest_pin_base(self):
        card = (REPO / "models" / "slm-tagger" / "v1" / "MODEL_CARD.md").read_text(encoding="utf-8")
        manifest = json.loads(
            (REPO / "models" / "slm-tagger" / "v1" / "manifest.json").read_text(encoding="utf-8")
        )
        assert "Radon SLM tagger: internal specialist, not a SotA replacement for open research." in card
        assert manifest["base"] == SLM_BASE_ID


class TestTrainSh:
    def test_refuses_api_key(self, tmp_path):
        env = {**os.environ, "ANTHROPIC_API_KEY": "sk-test-prepaid"}
        proc = subprocess.run(
            ["bash", str(TRAIN)],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 2
        assert "ANTHROPIC_API_KEY" in proc.stderr

    def test_refuses_non_turso_sources(self, tmp_path):
        data = tmp_path / "data" / "slm" / "tagger" / "v1"
        data.mkdir(parents=True)
        (data / "manifest.json").write_text(
            json.dumps({"sources": ["scraped.news"]}), encoding="utf-8"
        )
        env = {k: v for k, v in os.environ.items() if not k.endswith("_API_KEY")}
        env["SLM_DATA_DIR"] = str(data)
        proc = subprocess.run(
            ["bash", str(TRAIN)],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 2
        assert "turso.posts" in proc.stderr
