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
