"""JS GitHub Actions must declare Node 24, not Node 20.

The runner warning names checkout / setup-python / upload-artifact pinned
to Node-20 action.yml. Official majors that switched to ``using: node24``
are the fix. setup-bun v2.2.0 is already node24 at the existing pin.
"""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"

# Official node24 releases, SHA-pinned (immutable).
NODE24_PINS = {
    "actions/checkout": (
        "3d3c42e5aac5ba805825da76410c181273ba90b1",  # v7.0.1
    ),
    "actions/setup-python": (
        "5fda3b95a4ea91299a34e894583c3862153e4b97",  # v7.0.0
    ),
    "actions/upload-artifact": (
        "043fb46d1a93c77aae656e7c1c64a875d1fc6a0a",  # v7.0.1
    ),
}

# Last Node-20 pins this repo used. Must not reappear.
NODE20_PINS = {
    "34e114876b0b11c390a56381ad16ebd13914f8d5",  # checkout v4
    "a26af69be951a213d495a4c3e4e4022e16d87065",  # setup-python v5
    "ea165f8d65b6e75b540449e92b4886f43607fa02",  # upload-artifact v4
}


def _uses() -> list[str]:
    found = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        for job in (workflow.get("jobs") or {}).values():
            for step in job.get("steps") or []:
                uses = step.get("uses")
                if uses:
                    found.append(uses)
    return found


def test_official_js_actions_use_node24_pins() -> None:
    uses = _uses()
    for action, shas in NODE24_PINS.items():
        matches = [u for u in uses if u.startswith(action + "@")]
        assert matches, f"missing {action}"
        for pin in matches:
            sha = pin.split("@", 1)[1]
            assert sha in shas, f"{pin} is not a node24 pin {shas}"


def test_node20_action_pins_are_gone() -> None:
    blob = "\n".join(p.read_text(encoding="utf-8") for p in WORKFLOWS.glob("*.yml"))
    for sha in NODE20_PINS:
        assert sha not in blob, f"stale node20 pin {sha}"
