"""The bootstrap must never blank a provider key it was asked to carry.

2026-09-07: `~/.radon/agent-cli/env` came back with `NVIDIA_API_KEY=` and
`CEREBRAS_API_KEY=` empty even though both were exported. The cause is a bash
3.2 trap — /bin/bash on this runner is 3.2 — in

    local key="$1" cur="${2:-}" val="${!key:-}"

where the indirect expansion runs before `key` is visible, so `val` is empty and
the carry silently writes nothing. Both fallback rungs then fail
`provider_ready` and the ladder skips straight past NVIDIA and Cerebras.

These run the real script against a temporary root; none of them touch the
network or the operator's own agent-cli directory.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
BOOTSTRAP = REPO / "scripts" / "agent_cli_bootstrap.sh"
# 3.2 is what launchd hands the nightly job; the bug is invisible under 5.x.
BASH = "/bin/bash"


def _run(tmp_path: Path, env_extra: dict[str, str]) -> subprocess.CompletedProcess:
    env = {
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "HOME": str(tmp_path / "home"),
        "RADON_AGENT_CLI_ROOT": str(tmp_path / "agent-cli"),
        # No key reaches curl, so /v1/models is never called.
        **env_extra,
    }
    (tmp_path / "home").mkdir(exist_ok=True)
    return subprocess.run(
        [BASH, str(BOOTSTRAP)], env=env, capture_output=True, text=True, timeout=120
    )


def _env_file(tmp_path: Path) -> dict[str, str]:
    path = tmp_path / "agent-cli" / "env"
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k] = v
    return out


@pytest.mark.skipif(not Path(BASH).exists(), reason="no /bin/bash")
def test_an_exported_key_reaches_the_env_file(tmp_path):
    _run(tmp_path, {"NVIDIA_API_KEY": "nvapi-TEST", "CEREBRAS_API_KEY": "csk-TEST"})
    written = _env_file(tmp_path)
    assert written["NVIDIA_API_KEY"] == "nvapi-TEST", (
        "an exported key was blanked on the way to the env file; the nvidia "
        f"rung would be skipped every night: {written}"
    )
    assert written["CEREBRAS_API_KEY"] == "csk-TEST", written


@pytest.mark.skipif(not Path(BASH).exists(), reason="no /bin/bash")
def test_a_rerun_without_the_key_carries_the_stored_one_forward(tmp_path):
    """Re-running from a shell that lacks the key must not erase it."""
    _run(tmp_path, {"NVIDIA_API_KEY": "nvapi-TEST", "CEREBRAS_API_KEY": "csk-TEST"})
    _run(tmp_path, {})
    written = _env_file(tmp_path)
    assert written["NVIDIA_API_KEY"] == "nvapi-TEST", (
        f"a re-run without the key in the environment erased it: {written}"
    )
    assert written["CEREBRAS_API_KEY"] == "csk-TEST", written


@pytest.mark.skipif(not Path(BASH).exists(), reason="no /bin/bash")
def test_the_rung_key_is_stable_and_the_model_id_lives_only_here(tmp_path):
    """The ladder names a config KEY; the vendor id is resolved at provision
    time so a new model release needs no wrapper edit."""
    _run(tmp_path, {"NVIDIA_API_KEY": "nvapi-TEST", "CEREBRAS_API_KEY": "csk-TEST"})
    for tag, key in (("nvidia", "nvidia-latest"), ("cerebras", "cerebras-latest")):
        cfg = (tmp_path / "agent-cli" / f"grok-home-{tag}" / "config.toml").read_text(
            encoding="utf-8"
        )
        assert f'[model."{key}"]' in cfg, (tag, cfg)
        assert "model = " in cfg, (tag, cfg)


def test_no_local_declaration_expands_an_indirection_it_also_assigns():
    """The bash 3.2 trap itself, pinned at the source so it cannot come back."""
    body = BOOTSTRAP.read_text(encoding="utf-8")
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped.startswith("local "):
            continue
        names = []
        for part in stripped[len("local ") :].split():
            name, _, _ = part.partition("=")
            if "${!" in part:
                referenced = part.split("${!", 1)[1].split(":", 1)[0].rstrip('}"')
                assert referenced not in names, (
                    "bash 3.2 expands ${!x} before x is visible when both are "
                    f"assigned in one `local`; split them: {stripped}"
                )
            names.append(name)
