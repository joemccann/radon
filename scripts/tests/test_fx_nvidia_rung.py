"""The documentation loop runs on Vercel fx driving NVIDIA NIM, and nothing else.

2026-09-26: every documentation phase died on its first model call. The NVIDIA
rung was hosted by the grok CLI, which exited 1 on `serialization error:
invalid type: null, expected u32` while parsing NIM's reply, and the loop has
no business on the codex or grok accounts anyway. fx speaks OpenAI Chat
Completions to NVIDIA directly and records an unreported count as null.

The second failure was PATH: fx runs every shell command in the account's
login shell (`zsh -l -i`, resolved from getpwuid, not $SHELL). That re-sources
the operator profile and /etc/zprofile, so `python3.13` became Homebrew's and
the audit's own `test_path_filter.py` failed on `No module named 'yaml'` while
the loop's venv had it. The wrapper now hands fx a runner-owned ZDOTDIR whose
startup files restore the PATH the wrapper built.
"""

from __future__ import annotations

import importlib.util
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_H = Path(__file__).with_name("_loop_harness.py")
_spec = importlib.util.spec_from_file_location("_loop_harness_fx", _H)
_h = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _h
_spec.loader.exec_module(_h)

WRAPPER = _h.LOOPS["documentation"]


def _fx_env(tmp_path):
    raw = (tmp_path / "attempts.tsv.fx-env").read_text(encoding="utf-8")
    env = {}
    for line in raw.splitlines():
        key, _, val = line.partition("=")
        env[key] = val
    return env


def test_the_default_ladder_is_the_single_fx_nvidia_rung():
    body = WRAPPER.read_text(encoding="utf-8")
    m = re.search(
        r'^PROVIDER_LADDER="\$\{RADON_WEEKEND_PROVIDER_LADDER:-(.+?)\}"$', body, re.M
    )
    assert m, "no default provider ladder"
    assert m.group(1).split() == ["fx:nvidia"], m.group(1)


def test_an_operator_ladder_still_overrides_the_default(tmp_path):
    _proc, tried, _calls, _argv = _h._run_multi(
        tmp_path, "documentation", "audit",
        provider_ladder="grok:grok-4.6 fx:nvidia", capped_providers=("grok",),
    )
    assert [t.split(":", 1)[0] for t in tried] == ["grok", "fx"], tried


def test_every_phase_launches_fx_and_nothing_else(tmp_path):
    for phase in ("audit", "remediate", "deliver"):
        sub = tmp_path / phase
        sub.mkdir()
        proc, tried, _calls, argv = _h._run_multi(sub, "documentation", phase)
        assert tried == ["fx:nvidia"], (phase, tried, proc.stdout, proc.stderr)
        assert argv[0].split() == ["ask", "--full-access", "--no-save"], argv


def test_fx_gets_the_prompt_on_stdin_in_the_repo(tmp_path):
    _proc, tried, _calls, _argv = _h._run_multi(tmp_path, "documentation", "audit")
    assert tried == ["fx:nvidia"], tried
    env = _fx_env(tmp_path)
    assert env["STDIN"] == "stub", env
    assert env["PWD"] == str(tmp_path / "clone"), env


def test_fx_runs_non_interactive_and_bills_only_nvidia(tmp_path):
    _h._run_multi(tmp_path, "documentation", "audit")
    env = _fx_env(tmp_path)
    assert env["FX_PROVIDER"] == "nvidia", env
    assert env["FX_AUTO_UPGRADE"] == "0", env
    assert env["FX_SKIP_ONBOARDING"] == "1", env
    assert env["FX_DISABLE_KEYCHAIN"] == "1", env
    assert env["NVIDIA_API_KEY"] == "stub-key", env
    assert env["XAI_API_KEY"] == "<unset>", env
    assert env["CEREBRAS_API_KEY"] == "<unset>", env
    assert env["RADON_WEEKEND_REDUCED"] == "1", env


def test_fx_carries_the_wrapper_path_into_its_login_shell(tmp_path):
    _h._run_multi(tmp_path, "documentation", "audit")
    env = _fx_env(tmp_path)
    assert env["RADON_AGENT_PATH"] == env["PATH"], env
    zdot = Path(env["ZDOTDIR"])
    assert zdot == tmp_path / "agent-cli" / "fx-zdotdir", env
    for name in (".zshrc", ".zlogin"):
        body = (zdot / name).read_text(encoding="utf-8")
        assert 'export PATH="$RADON_AGENT_PATH"' in body, (name, body)


def test_the_zdotdir_is_rewritten_every_round(tmp_path):
    """Self-healing: a hand edit or a deleted file is back the next round."""
    zdot = tmp_path / "agent-cli" / "fx-zdotdir"
    zdot.mkdir(parents=True)
    (zdot / ".zshrc").write_text("export PATH=/opt/homebrew/bin\n", encoding="utf-8")
    _h._run_multi(tmp_path, "documentation", "audit")
    assert 'export PATH="$RADON_AGENT_PATH"' in (zdot / ".zshrc").read_text()
    assert (zdot / ".zlogin").exists()


@pytest.mark.skipif(not Path("/bin/zsh").exists(), reason="needs zsh")
def test_a_real_login_shell_resolves_the_venv_first(tmp_path):
    """The shape fx uses: `zsh -l -i -c`. Whatever the profile stage does to
    PATH (/etc/zprofile's path_helper, an operator .zprofile), the restore in
    the wrapper-written .zshrc/.zlogin runs after it and the venv wins."""
    _h._run_multi(tmp_path, "documentation", "audit")
    written = tmp_path / "agent-cli" / "fx-zdotdir"
    zdot = tmp_path / "zdot"
    zdot.mkdir()
    for name in (".zshrc", ".zlogin"):
        shutil.copy2(written / name, zdot / name)
    venv = tmp_path / "venv" / "bin"
    decoy = tmp_path / "decoy"
    for d, word in ((venv, "venv"), (decoy, "decoy")):
        d.mkdir(parents=True)
        exe = d / "python3.13"
        exe.write_text(f"#!/bin/sh\necho {word}\n", encoding="utf-8")
        exe.chmod(0o755)
    # The profile stage puts something else first, as the Mini's does.
    (zdot / ".zprofile").write_text(f'export PATH="{decoy}:$PATH"\n', encoding="utf-8")
    agent_path = f"{venv}:/usr/bin:/bin"

    def resolve(with_restore):
        env = {"HOME": str(tmp_path), "PATH": agent_path, "ZDOTDIR": str(zdot), "TERM": "dumb"}
        if with_restore:
            env["RADON_AGENT_PATH"] = agent_path
        out = subprocess.run(
            ["/bin/zsh", "-l", "-i", "-c", "command -v python3.13"],
            env=env, capture_output=True, text=True, timeout=30,
            stdin=subprocess.DEVNULL,
        )
        return out.stdout.strip().splitlines()[-1]

    assert resolve(False) == str(decoy / "python3.13"), "the profile stage must win without the restore"
    assert resolve(True) == str(venv / "python3.13")


def test_an_uninstalled_fx_is_an_honest_incomplete(tmp_path):
    proc, tried, calls, _argv = _h._run_multi(
        tmp_path, "documentation", "audit",
        installed=("claude", "codex", "grok"),
    )
    assert tried == [], tried
    assert proc.returncode == 75, (proc.returncode, proc.stdout, proc.stderr)
    assert "all agent providers exhausted" in calls, calls


def test_fx_without_an_nvidia_key_is_skipped(tmp_path):
    proc, tried, _calls, _argv = _h._run_multi(
        tmp_path, "documentation", "audit",
        authed=("claude", "codex", "grok"),
    )
    assert tried == [], tried
    assert proc.returncode == 75, (proc.returncode, proc.stdout, proc.stderr)


def test_an_fx_rate_limit_is_classified_not_a_bare_failure(tmp_path):
    proc, tried, calls, _argv = _h._run_multi(
        tmp_path, "documentation", "audit",
        capped_providers=("fx",),
        cap_line="failed: rate_limited · retry after: 4s",
    )
    assert tried == ["fx:nvidia"], tried
    assert proc.returncode == 75, (proc.returncode, proc.stdout, proc.stderr)
    assert "all agent providers exhausted" in calls, calls


def test_the_fx_arm_is_wrapped_in_timeout():
    body = WRAPPER.read_text(encoding="utf-8")
    start = body.index("    fx)\n", body.index("launch_round() {"))
    arm = body[start : body.index(";;", start)]
    assert '"$TIMEOUT_BIN" -k "$KILL_AFTER_SECS"' in arm, arm
    assert '< "$prompt_file"' in arm, arm
    assert os.sep + "fx-zdotdir" in arm, arm
