"""HR-2 / HR-5 train config pins and train.sh refusals."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from newsfeed.slm.contract import SLM_BASE_ID

REPO = Path(__file__).resolve().parents[2]
MLX_YAML = REPO / "scripts" / "newsfeed" / "slm" / "configs" / "qwen25-1p5b-qlora-v1.yaml"
LF_YAML = REPO / "scripts" / "newsfeed" / "slm" / "configs" / "llamafactory-qwen25-1p5b-qlora-v1.yaml"
DATASET_INFO = REPO / "scripts" / "newsfeed" / "slm" / "configs" / "dataset_info.json"
TRAIN = REPO / "scripts" / "newsfeed" / "slm" / "train.sh"


class TestConfig:
    def test_llamafactory_is_default_and_pins_base(self):
        text = LF_YAML.read_text(encoding="utf-8")
        sh = TRAIN.read_text(encoding="utf-8")
        info = json.loads(DATASET_INFO.read_text(encoding="utf-8"))
        assert SLM_BASE_ID == "Qwen/Qwen2.5-1.5B-Instruct"
        assert f"model_name_or_path: {SLM_BASE_ID}" in text
        assert "finetuning_type: lora" in text
        assert "quantization_bit: 4" in text
        assert "train_on_prompt: false" in text
        assert "val_size" not in text
        assert "shuffle" not in text
        assert "report_to: none" in text
        assert "llamafactory-cli" in sh
        assert 'SLM_TRAINER:-llamafactory' in sh
        assert "radon_slm_tagger" in info
        assert info["radon_slm_tagger"]["formatting"] == "sharegpt"
        assert info["radon_slm_tagger"]["columns"]["messages"] == "messages"

    def test_mlx_alt_still_pins_mask_prompt(self):
        text = MLX_YAML.read_text(encoding="utf-8")
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
        assert "LLaMA-Factory" in card
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
