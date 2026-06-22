"""FU4 — launchd timer wiring for the new F3/F4/F5 fetchers.

Pure file assertions: each new plist exists, parses as valid plist XML, carries a
``com.radon.*`` Label, references the correct run-wrapper script, and is wired
into ``scripts/local.sh`` load/unload. No network, no DB, no IB.
"""
from __future__ import annotations

import plistlib
from pathlib import Path
from xml.dom import minidom

import pytest

_PROJECT_DIR = Path(__file__).resolve().parents[2]
_CONFIG_DIR = _PROJECT_DIR / "config"
_SCRIPTS_DIR = _PROJECT_DIR / "scripts"
_LOCAL_SH = _SCRIPTS_DIR / "local.sh"

# label -> (plist filename, run-wrapper script it must reference)
_NEW_TIMERS = {
    "com.radon.catalysts": (
        "com.radon.catalysts.plist",
        "run_catalysts.sh",
    ),
    "com.radon.informed-flow": (
        "com.radon.informed-flow.plist",
        "run_informed_flow.sh",
    ),
    "com.radon.event-odds": (
        "com.radon.event-odds.plist",
        "run_event_odds.sh",
    ),
}


@pytest.mark.parametrize("label", sorted(_NEW_TIMERS))
def test_plist_file_exists(label: str) -> None:
    filename, _ = _NEW_TIMERS[label]
    assert (_CONFIG_DIR / filename).is_file(), f"missing plist: config/{filename}"


@pytest.mark.parametrize("label", sorted(_NEW_TIMERS))
def test_plist_is_valid_xml(label: str) -> None:
    filename, _ = _NEW_TIMERS[label]
    text = (_CONFIG_DIR / filename).read_text()
    # minidom raises on malformed XML
    minidom.parseString(text)


@pytest.mark.parametrize("label", sorted(_NEW_TIMERS))
def test_plist_parses_as_plist(label: str) -> None:
    filename, _ = _NEW_TIMERS[label]
    with (_CONFIG_DIR / filename).open("rb") as fh:
        parsed = plistlib.load(fh)
    assert isinstance(parsed, dict)
    assert parsed.get("Label") == label
    assert label.startswith("com.radon.")


@pytest.mark.parametrize("label", sorted(_NEW_TIMERS))
def test_plist_references_run_wrapper(label: str) -> None:
    filename, wrapper = _NEW_TIMERS[label]
    with (_CONFIG_DIR / filename).open("rb") as fh:
        parsed = plistlib.load(fh)
    program_args = parsed.get("ProgramArguments")
    assert isinstance(program_args, list) and program_args
    joined = " ".join(program_args)
    assert wrapper in joined, f"{filename} must invoke {wrapper}"


@pytest.mark.parametrize("label", sorted(_NEW_TIMERS))
def test_plist_has_schedule_and_logs(label: str) -> None:
    filename, _ = _NEW_TIMERS[label]
    with (_CONFIG_DIR / filename).open("rb") as fh:
        parsed = plistlib.load(fh)
    has_cadence = "StartCalendarInterval" in parsed or "StartInterval" in parsed
    assert has_cadence, f"{filename} must define a cadence"
    assert parsed.get("StandardOutPath"), f"{filename} missing StandardOutPath"
    assert parsed.get("StandardErrorPath"), f"{filename} missing StandardErrorPath"
    assert parsed.get("WorkingDirectory"), f"{filename} missing WorkingDirectory"


@pytest.mark.parametrize("label", sorted(_NEW_TIMERS))
def test_local_sh_references_label(label: str) -> None:
    text = _LOCAL_SH.read_text()
    assert label in text, f"scripts/local.sh must reference {label}"


def test_run_wrappers_exist() -> None:
    for _, (_, wrapper) in _NEW_TIMERS.items():
        assert (_SCRIPTS_DIR / wrapper).is_file(), f"missing wrapper scripts/{wrapper}"
