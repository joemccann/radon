"""HR-2 / HR-5 train config pins and train.sh refusals."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys

import pytest
from pathlib import Path

from newsfeed.slm.contract import SLM_BASE_ID

REPO = Path(__file__).resolve().parents[2]
MLX_YAML = REPO / "scripts" / "newsfeed" / "slm" / "configs" / "qwen25-1p5b-qlora-v1.yaml"
LF_YAML = REPO / "scripts" / "newsfeed" / "slm" / "configs" / "llamafactory-qwen25-1p5b-qlora-v1.yaml"
QWEN35_YAML = REPO / "scripts" / "newsfeed" / "slm" / "configs" / "llamafactory-qwen35-2b-qlora-v1.yaml"
QWEN35_REVISION = "15852e8c16360a2fea060d615a32b45270f8a8fc"
DATASET_INFO = REPO / "scripts" / "newsfeed" / "slm" / "configs" / "dataset_info.json"
TRAIN = REPO / "scripts" / "newsfeed" / "slm" / "train.sh"
REQS = REPO / "scripts" / "newsfeed" / "slm" / "requirements-slm.txt"


def _req_pins(text: str) -> dict[str, str]:
    pins: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, sep, version = line.partition("==")
        if sep:
            pins[name.strip()] = version.split()[0]
    return pins


def _yaml_fields(text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in text.splitlines():
        if not line or line.lstrip().startswith("#"):
            continue
        key, sep, value = line.partition(":")
        if sep:
            fields[key.strip()] = value.split("#", 1)[0].strip()
    return fields


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
        reqs = (REPO / "scripts" / "newsfeed" / "slm" / "requirements-slm.txt").read_text(encoding="utf-8")
        assert "llamafactory" in reqs
        assert "Train-only" in reqs
        assert "radon_slm_tagger" in info
        assert info["radon_slm_tagger"]["formatting"] == "sharegpt"
        assert info["radon_slm_tagger"]["columns"]["messages"] == "messages"

    def test_llamafactory_pins_40_hex_model_revision(self):
        # LLaMA-Factory's ModelArguments field is model_revision. loader.py
        # forwards it as from_pretrained(revision=...). A bare `revision` key
        # is unused and HfArgumentParser rejects it unless ALLOW_EXTRA_ARGS.
        text = LF_YAML.read_text(encoding="utf-8")
        pins = json.loads((REPO / "cloud" / "gpu" / "pins.json").read_text(encoding="utf-8"))
        fields = {}
        for line in text.splitlines():
            if not line or line.lstrip().startswith("#"):
                continue
            key, sep, value = line.partition(":")
            if sep:
                fields[key.strip()] = value.split("#", 1)[0].strip()
        revision = fields.get("model_revision", "")
        assert re.fullmatch(r"[0-9a-f]{40}", revision)
        assert revision == "989aa7980e4cf806f80c7fef2b1adb7bc71aa306"
        assert revision == pins["model_revision"]
        assert "revision" not in fields
        sh = TRAIN.read_text(encoding="utf-8")
        assert 'llamafactory-cli train "$LF_CONFIG"' in sh

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


class TestQwen35Candidate:
    def test_pins_revision_template_and_refuses_remote_code(self):
        fields = _yaml_fields(QWEN35_YAML.read_text(encoding="utf-8"))
        assert fields["model_name_or_path"] == "Qwen/Qwen3.5-2B"
        assert re.fullmatch(r"[0-9a-f]{40}", fields["model_revision"])
        assert fields["model_revision"] == QWEN35_REVISION
        assert fields["model_revision"] != "989aa7980e4cf806f80c7fef2b1adb7bc71aa306"
        assert "revision" not in fields
        assert fields["trust_remote_code"] == "false"
        assert fields["template"] == "qwen3_5_nothink"
        assert fields["enable_thinking"] == "false"
        assert "in_proj_qkv" in fields["lora_target"].split(",")
        assert "out_proj" in fields["lora_target"].split(",")
        assert "q_proj" in fields["lora_target"].split(",")
        assert fields["cutoff_len"] == "1024"
        assert fields["learning_rate"] == "1.0e-4"
        assert fields["train_on_prompt"] == "false"
        assert fields["freeze_vision_tower"] == "true"
        assert fields["freeze_multi_modal_projector"] == "true"
        assert fields["output_dir"] != "models/slm-tagger/v1/adapter"
        reqs = _req_pins(REQS.read_text(encoding="utf-8"))
        assert reqs["llamafactory"] == "0.9.5"
        assert reqs["transformers"] == "5.6.0"
        assert reqs["torch"] == "2.14.1"
        assert reqs["peft"] == "0.18.1"
        assert reqs["bitsandbytes"] == "0.50.2"
        assert reqs["accelerate"] == "1.11.0"
        assert reqs["datasets"] == "4.0.0"
        assert reqs["trl"] == "0.24.0"
        assert reqs["tokenizers"] == "0.22.2"
        fleet = (REPO / "requirements.txt").read_text(encoding="utf-8")
        assert "llamafactory" not in fleet
        assert "transformers" not in fleet
        assert "tokenizers==0.23.1" in fleet
        assert "huggingface-hub==1.24.0" in fleet
        sh = TRAIN.read_text(encoding="utf-8")
        assert "SLM_LF_CONFIG" in sh
        assert "llamafactory-qwen25-1p5b-qlora-v1.yaml" in sh

    def test_slm_lf_config_selects_candidate_and_default_stays_baseline(self, tmp_path):
        data = tmp_path / "data"
        data.mkdir()
        (data / "manifest.json").write_text(json.dumps({"sources": ["turso.posts"]}), encoding="utf-8")
        binaries = tmp_path / "bin"
        binaries.mkdir()
        captured = tmp_path / "argv.json"
        trainer = binaries / "llamafactory-cli"
        trainer.write_text(
            f"#!{sys.executable}\nimport json, sys\n"
            f"open({str(captured)!r}, 'w').write(json.dumps(sys.argv))\n",
            encoding="utf-8",
        )
        trainer.chmod(0o755)
        env = {
            "PATH": f"{binaries}{os.pathsep}{Path(sys.executable).parent}{os.pathsep}{os.defpath}",
            "SLM_DATA_DIR": str(data),
        }
        proc = subprocess.run(
            ["bash", str(TRAIN)], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30
        )
        assert proc.returncode == 0, proc.stderr
        argv = json.loads(captured.read_text(encoding="utf-8"))
        assert str(LF_YAML) in argv
        assert str(QWEN35_YAML) not in argv

        env["SLM_LF_CONFIG"] = str(QWEN35_YAML)
        proc = subprocess.run(
            ["bash", str(TRAIN)], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30
        )
        assert proc.returncode == 0, proc.stderr
        argv = json.loads(captured.read_text(encoding="utf-8"))
        assert str(QWEN35_YAML) in argv
        assert str(LF_YAML) not in argv


class TestRemoteCode:
    def test_no_slm_config_enables_remote_model_code(self):
        configs = sorted((REPO / "scripts" / "newsfeed" / "slm" / "configs").glob("*.yaml"))
        assert configs
        for cfg in configs:
            for line in cfg.read_text(encoding="utf-8").splitlines():
                key, _, value = line.partition(":")
                if key.strip() == "trust_remote_code":
                    assert value.split("#")[0].strip().lower() == "false", cfg.name

    def test_trainer_runs_under_an_env_allowlist(self, tmp_path):
        data = tmp_path / "data"
        data.mkdir()
        (data / "manifest.json").write_text(json.dumps({"sources": ["turso.posts"]}), encoding="utf-8")
        bindir = tmp_path / "bin"
        bindir.mkdir()
        dump = tmp_path / "env.txt"
        fake = bindir / "llamafactory-cli"
        fake.write_text(f"#!/bin/sh\nenv > {dump}\n", encoding="utf-8")
        fake.chmod(0o755)
        env = {k: v for k, v in os.environ.items() if not k.endswith("_API_KEY")}
        env.update(
            PATH=f"{bindir}{os.pathsep}{env.get('PATH', '/usr/bin:/bin')}",
            SLM_DATA_DIR=str(data),
            HF_HOME=str(tmp_path / "hf"),
            CUDA_VISIBLE_DEVICES="0",
            TURSO_AUTH_TOKEN="qzTURSONOTREAL",
            GH_TOKEN="qzGHNOTREAL",
        )
        proc = subprocess.run(["bash", str(TRAIN)], cwd=tmp_path, env=env, capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr
        seen = dump.read_text(encoding="utf-8")
        assert "NOTREAL" not in seen
        assert f"HF_HOME={tmp_path / 'hf'}" in seen
        assert "CUDA_VISIBLE_DEVICES=0" in seen
        assert f"PATH={bindir}" in seen


@pytest.mark.parametrize("credential_name", [
    "HF_HUB_TOKEN", "HF_HUB_ACCESS_TOKEN", "HF_DATASETS_TOKEN",
    "TRANSFORMERS_TOKEN", "CUDA_AUTH_TOKEN", "NVIDIA_NGC_TOKEN",
    "PYTORCH_PASSWORD", "TORCH_SECRET",
])
def test_trainer_namespace_never_forwards_credentials(tmp_path, credential_name):
    """REL-319 / R-731: allowed namespace prefixes are not safe values."""
    data = tmp_path / "data"
    data.mkdir()
    (data / "manifest.json").write_text(json.dumps({"sources": ["turso.posts"]}))
    binaries = tmp_path / "bin"
    binaries.mkdir()
    captured = tmp_path / "names.json"
    trainer = binaries / "llamafactory-cli"
    trainer.write_text(
        f"#!{sys.executable}\nimport json, os\n"
        f"open({str(captured)!r}, 'w').write(json.dumps(sorted(os.environ)))\n"
    )
    trainer.chmod(0o755)
    env = {
        "PATH": f"{binaries}{os.pathsep}{Path(sys.executable).parent}{os.pathsep}{os.defpath}",
        "SLM_DATA_DIR": str(data),
        credential_name: "synthetic-credential-only",
        "HF_HUB_CACHE": str(tmp_path / "cache"),
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "CUDA_VISIBLE_DEVICES": "0",
    }
    proc = subprocess.run(["bash", str(TRAIN)], cwd=tmp_path, env=env,
                          capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    names = json.loads(captured.read_text())
    assert credential_name not in names
    assert {"PATH", "HF_HUB_CACHE", "HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE",
            "CUDA_VISIBLE_DEVICES"} <= set(names)
