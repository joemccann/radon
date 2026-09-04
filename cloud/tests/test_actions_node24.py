"""JS GitHub Actions must declare Node 24, not Node 20.

The runner warning names checkout / setup-python / upload-artifact pinned
to Node-20 action.yml. Official majors that switched to ``using: node24``
are the fix. setup-bun v2.2.0 is already node24 at the existing pin.
CI Python now uses astral-sh/setup-uv (node24) instead of setup-python.
"""

from __future__ import annotations

import re
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
    "astral-sh/setup-uv": (
        "803947b9bd8e9f986429fa0c5a41c367cd732b41",  # v7.2.1
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


# Binaries fetched inside `run:` steps are pinned like actions are. A
# `releases/latest` download is a mutable reference: the artifact under it
# changes without any diff in this repo.
def _run_scripts() -> list[tuple[str, str]]:
    found = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        for job_name, job in (workflow.get("jobs") or {}).items():
            for step in job.get("steps") or []:
                run = step.get("run")
                if run:
                    found.append((f"{path.name}:{job_name}:{step.get('name', '')}", run))
    return found


def test_run_steps_never_download_releases_latest() -> None:
    offenders = [where for where, run in _run_scripts() if "releases/latest" in run]
    assert offenders == [], f"unpinned releases/latest download in {offenders}"


def test_caddy_install_is_version_pinned_and_checksum_verified() -> None:
    steps = [run for where, run in _run_scripts() if "caddyserver/caddy" in run]
    assert steps, "no workflow step installs caddy"
    for run in steps:
        assert re.search(r'ver="\d+\.\d+\.\d+"', run), run
        assert "_checksums.txt" in run, run
        assert "sha512sum -c --ignore-missing" in run, run


def test_ci_caddy_version_equals_the_production_apt_pin() -> None:
    """T-417: CI's frozen caddy must be the version production installs.

    The pin itself is correct supply-chain hardening, but it replaced a
    ``releases/latest`` fetch whose whole purpose was to let
    cloud/tests/test_caddy_edge_timeouts.py observe a ``lb_retry_match``
    semantics change. Frozen CI + an unpinned ``apt-get install -y caddy``
    against the Cloudsmith ``stable`` repo means CI tests a binary
    production stopped running, on the one path (POST /api/orders/place)
    that has no idempotency key and whose failure mode is a duplicate
    order. Version equality restores the signal: a bump is now a
    deliberate two-line diff that re-runs the edge tests.
    """
    ci_runs = [run for where, run in _run_scripts() if "caddyserver/caddy" in run]
    assert ci_runs, "no workflow step installs caddy"
    ci_versions = {m for run in ci_runs for m in re.findall(r'ver="(\d+\.\d+\.\d+)"', run)}
    assert len(ci_versions) == 1, f"workflows disagree on the caddy version: {ci_versions}"

    setup = (ROOT / "cloud" / "scripts" / "setup-vps.sh").read_text(encoding="utf-8")
    prod = re.findall(r'readonly CADDY_VERSION="(\d+\.\d+\.\d+)"', setup)
    assert len(prod) == 1, "setup-vps.sh must declare exactly one CADDY_VERSION"
    assert re.search(r'apt-get install -y caddy="\$\{CADDY_VERSION\}"', setup), (
        "setup-vps.sh must install the pinned caddy version, not the "
        "floating Cloudsmith `stable` head"
    )
    assert prod[0] == ci_versions.pop(), (
        "CI caddy `ver=` and setup-vps.sh CADDY_VERSION have drifted. "
        "CI would then exercise lb_retry_match on a binary production does "
        "not run. Bump both together."
    )
