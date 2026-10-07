"""T-538: observe every third-party trainer entry, including import discovery."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


REPO = Path(__file__).resolve().parents[2]
TRAIN = REPO / "scripts/newsfeed/slm/train.sh"
BASH = shutil.which("bash")


def run_trainer(tmp_path: Path, trainer: str, *, complete: bool, probe_rc: int = 0):
    """No host trainer, dataset, credential or network is reachable via the stubs."""
    binaries = tmp_path / "bin"
    binaries.mkdir()
    data = tmp_path / "data"
    data.mkdir()
    (data / "manifest.json").write_text(json.dumps({"sources": ["turso.posts"]}))
    receipts = tmp_path / "calls.jsonl"
    stub = binaries / "python3.13"
    stub.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "args = sys.argv[1:]\n"
        "if args[:1] == ['-']:\n"
        f"    os.execv({sys.executable!r}, [{sys.executable!r}, *args])\n"
        f"with open({str(receipts)!r}, 'a') as output:\n"
        "    output.write(json.dumps({'program': Path(sys.argv[0]).name, 'args': args, "
        "'env': dict(os.environ)}) + '\\n')\n"
        f"if args[:1] == ['-c']: sys.exit({probe_rc})\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    if trainer == "cli":
        (binaries / "llamafactory-cli").symlink_to(stub)
    # A sealed environment prevents host tokens or a real training installation
    # from entering the harness. Expected values are independent of train.sh.
    # LC_ALL=C prevents the Python receipt stub from adding LC_CTYPE during
    # locale coercion; missing runtime variables must stay missing.
    approved = {"PATH": f"{binaries}{os.pathsep}/usr/bin:/bin", "LC_ALL": "C"}
    if complete:
        approved.update({
            "HOME": str(tmp_path / "home"), "USER": "test-trainer", "LOGNAME": "test-trainer",
            "LANG": "C", "LC_ALL": "C", "TMPDIR": str(tmp_path), "TERM": "dumb",
            "VIRTUAL_ENV": str(tmp_path / "venv"), "CONDA_PREFIX": str(tmp_path / "conda"),
            "PYTHONPATH": str(tmp_path / "modules"), "HF_HOME": str(tmp_path / "hf cache"),
            "HF_HUB_OFFLINE": "1", "HF_DATASETS_CACHE": str(tmp_path / "datasets"),
            "TRANSFORMERS_OFFLINE": "1", "CUDA_VISIBLE_DEVICES": "0,2",
            "NVIDIA_VISIBLE_DEVICES": "0", "PYTORCH_ALLOC_CONF": "max_split_size_mb:128",
            "TORCH_HOME": str(tmp_path / "torch"), "OMP_NUM_THREADS": "2",
        })
    # Homebrew's Python framework adds __CF_USER_TEXT_ENCODING at startup.
    # Calibrate the receipt observer using ONLY the independent approved input,
    # rather than filtering any keys out of the actual trainer receipt.
    expected_env = json.loads(subprocess.check_output(
        [sys.executable, "-c", "import json, os; print(json.dumps(dict(os.environ)))"],
        env=approved, cwd=tmp_path, text=True, timeout=15,
    ))
    lf_config = tmp_path / "custom LF.yaml"
    mlx_config = tmp_path / "custom MLX.yaml"
    env = {
        **approved,
        "SLM_DATA_DIR": str(data), "SLM_MLX_CONFIG": str(mlx_config),
        "SLM_TRAINER": "mlx" if trainer == "mlx" else "llamafactory",
        "TURSO_AUTH_TOKEN": "qzTURSONOTREAL", "GH_TOKEN": "qzGHNOTREAL",
        "UW_TOKEN": "qzUWNOTREAL", "OPERATOR_PRIVATE": "qzUNKNOWNNOTREAL",
        "SLM_CONTROL_SECRET": "qzCONTROLNOTREAL",
    }
    proc = subprocess.run(
        [BASH, str(TRAIN), str(lf_config)], cwd=tmp_path, env=env,
        capture_output=True, text=True, timeout=15,
    )
    calls = [json.loads(line) for line in receipts.read_text().splitlines()] if receipts.exists() else []
    return proc, calls, expected_env, data, lf_config, mlx_config


@pytest.mark.parametrize("trainer", ["cli", "module", "mlx"])
@pytest.mark.parametrize("complete", [False, True], ids=["sparse", "complete"])
def test_every_trainer_entry_receives_only_approved_values(tmp_path, trainer, complete):
    proc, calls, expected_env, data, lf_config, mlx_config = run_trainer(tmp_path, trainer, complete=complete)
    assert proc.returncode == 0, proc.stderr
    if trainer == "cli":
        assert [(c["program"], c["args"]) for c in calls] == [
            ("llamafactory-cli", ["train", str(lf_config), f"dataset_dir={data}"]),
        ]
    elif trainer == "module":
        assert [(c["program"], c["args"]) for c in calls] == [
            ("python3.13", ["-c", "import llamafactory"]),
            ("python3.13", ["-m", "llamafactory.cli", "train", str(lf_config), f"dataset_dir={data}"]),
        ]
    else:
        assert [(c["program"], c["args"]) for c in calls] == [
            ("python3.13", ["-m", "mlx_lm", "lora", "--config", str(mlx_config)]),
        ]
    for call in calls:
        assert call["env"] == expected_env, call["args"]
    if trainer != "mlx":
        info = json.loads((data / "dataset_info.json").read_text())
        assert info["radon_slm_tagger"]["formatting"] == "sharegpt"
        assert info["radon_slm_tagger"]["columns"]["messages"] == "messages"
    else:
        assert not (data / "dataset_info.json").exists()


def test_failed_import_probe_is_isolated_and_does_not_start_training(tmp_path):
    proc, calls, expected_env, *_ = run_trainer(tmp_path, "module", complete=True, probe_rc=1)
    assert proc.returncode == 2
    assert "LLaMA-Factory is not installed" in proc.stderr
    assert len(calls) == 1
    assert calls[0]["args"] == ["-c", "import llamafactory"]
    assert calls[0]["env"] == expected_env
