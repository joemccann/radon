"""scripts/nightly_smoke.sh must cover every scheduled nightly loop.

The smoke script is a hand-run preflight: it is only useful if its loop table
names every loop launchd fires, with the clone, wrapper and skill each
launchd plist actually uses. A loop added or renamed without updating the
table would pass the smoke run while that loop could not start.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SMOKE = REPO / "scripts" / "nightly_smoke.sh"
BASH = shutil.which("bash") or "/bin/bash"


def _table() -> dict[str, tuple[str, str, str]]:
    rows = re.findall(
        r'^loop (\S+)\s+"\$W/(\S+)"\s+(\S+\.sh)\s+(\S+)\s+\S+\s+\S+$',
        SMOKE.read_text(encoding="utf-8"),
        re.M,
    )
    return {label: (clone, wrapper, skill) for label, clone, wrapper, skill in rows}


def _plists() -> dict[str, tuple[str, str]]:
    found = {}
    for plist in sorted((REPO / "config").glob("com.radon.*.plist")):
        text = plist.read_text(encoding="utf-8")
        m = re.search(r'exec /bin/bash "\$C/scripts/(\S+\.sh)" cycle', text)
        if not m:
            continue
        clone = re.search(r"C=[^;]*/radon-weekend/([^;/\s]+)", text)
        found[plist.name.removeprefix("com.radon.").removesuffix(".plist")] = (
            clone.group(1) if clone else "",
            m.group(1),
        )
    return found


def test_the_script_parses():
    assert subprocess.run([BASH, "-n", str(SMOKE)], capture_output=True).returncode == 0


def test_every_scheduled_loop_is_in_the_smoke_table():
    table, plists = _table(), _plists()
    assert plists, "no nightly plists found under config/"
    assert set(table) == set(plists)


def test_each_row_names_the_plists_wrapper_and_an_existing_skill():
    plists = _plists()
    for label, (_clone, wrapper, skill) in _table().items():
        assert wrapper == plists[label][1], label
        assert (REPO / "scripts" / wrapper).is_file(), wrapper
        assert (REPO / ".claude" / "skills" / skill / "SKILL.md").is_file(), skill


def test_each_row_names_the_plists_clone():
    plists = _plists()
    for label, (clone, _wrapper, _skill) in _table().items():
        if plists[label][0]:
            assert clone == plists[label][0], label


def test_origin_main_is_read_through_the_host_gitdir():
    # A clone's .git points at its agent gitdir, which the loop agent can
    # write; the hand-run preflight must read blobs through the host gitdir.
    text = SMOKE.read_text(encoding="utf-8")
    assert re.search(r'^REF="\$W/\.gitdirs/[a-z-]+\.git"', text, re.M)
    assert "git -C" not in text
    assert "core.hooksPath" in text.split("\nloop() {")[0]


@pytest.mark.parametrize(
    "wrapper",
    ["reliability_weekend.sh", "testing_weekend.sh", "documentation_nightly.sh"],
)
def test_the_extracted_readiness_check_passes_a_ready_fx_rung(tmp_path, wrapper):
    """2026-09-27: the smoke copied provider_ready without fx_key, so every
    fx rung printed `fx_key: command not found` and read as NOT ready."""
    text = SMOKE.read_text(encoding="utf-8")
    awk = re.search(r"fns=\"\$\(awk '([^']+)' \"\$wf\"\)\"", text).group(1)
    fns = subprocess.run(
        ["awk", awk, str(REPO / "scripts" / wrapper)], capture_output=True, text=True, check=True
    ).stdout
    home, cli = tmp_path / "home", tmp_path / "cli"
    (home / ".fx").mkdir(parents=True)
    (home / ".fx" / "settings.json").write_text("{}")
    cli.mkdir()
    (cli / "env").write_text("NVIDIA_API_KEY=k\n")
    fx = tmp_path / "fx"
    fx.write_text("#!/bin/sh\n")
    fx.chmod(0o755)
    call = re.search(r"provider_ready '\$\{r%%:\*\}'[^\"]*", text).group(0)
    script = fns + "\nr=fx:nvidia\n" + call.replace("'${r%%:*}'", "fx").replace("\\$", "$").replace('\\"', '"')
    proc = subprocess.run(
        [BASH, "-c", script], capture_output=True, text=True,
        env={"HOME": str(home), "PATH": "/usr/bin:/bin", "AGENT_CLI_ROOT": str(cli),
             "RADON_WEEKEND_FX_BIN": str(fx)},
    )
    assert "command not found" not in proc.stderr, proc.stderr
    assert proc.returncode == 0, proc.stderr


def test_the_ladder_helper_is_blobbed_not_read_from_the_clone():
    """DS-2026-09-28-01: the preflight must not execute a clone-resident helper.

    `security_claude_ladder.sh` resolves its python helper from `$REPO`, and
    the smoke sets `REPO="$C"` (the agent-writable clone), so a tampered
    `security_claude_ladder.py` would run in the hand-run preflight even
    though the ladder shell itself is read from `origin/main`.
    """
    text = SMOKE.read_text(encoding="utf-8")
    block = text.split("blob scripts/security_claude_ladder.sh", 1)[1].split("\n\n", 1)[0]
    assert "blob scripts/security_claude_ladder.py" in block
    assert "RADON_SECURITY_CLAUDE_LADDER_PY=" in block
