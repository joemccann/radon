"""Repo-side install acknowledgment for systemd units in services/.

Incident 2026-08-08: commit 0af0beab changed radon-catalysts.timer's schedule
(one 10:30 UTC OnCalendar -> three ET refreshes) in cloud/services/, but the CI
deploy does not install systemd units -- that is a separate root step, and the
control-plane bootstrap deliberately ignores non-control-plane units like this
timer. Nothing at commit time forced an acknowledgment that live and repo would
diverge, so production kept the old single-refresh schedule until the drift
audit raised `unit-mismatch:radon-catalysts.timer` as a config-drift error row.

Contract (mirrors config/drift-allowlist.conf's acknowledged-drift pattern):

  * config/installed-units.sha256 records `<sha256>  <unit-name>` for every
    unit in services/ as last installed on the host. It is bumped in the same
    commit as (or immediately after) the root install-copy.
  * A commit that changes a unit WITHOUT bumping its manifest entry must add a
    drift-allowlist acknowledgment for that unit (`unit-mismatch:<name>` or
    `not-installed:<name>`) so the pending-install window is explicit and the
    config-drift row stays green instead of paging.

Either path makes "unit changed in repo, host not updated" visible at review
time instead of as a production incident.
"""

import hashlib
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
SERVICES_DIR = ROOT / "services"
MANIFEST = ROOT / "config" / "installed-units.sha256"
ALLOWLIST = ROOT / "config" / "drift-allowlist.conf"

ACKNOWLEDGING_DRIFT_PREFIXES = ("unit-mismatch:", "not-installed:")


def _unit_files() -> dict[str, pathlib.Path]:
    return {
        p.name: p
        for p in SERVICES_DIR.iterdir()
        if p.is_file() and p.suffix in {".service", ".timer"}
    }


def _manifest_hashes() -> dict[str, str]:
    entries: dict[str, str] = {}
    for line in MANIFEST.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        digest, _, name = line.partition("  ")
        entries[name.strip()] = digest.strip()
    return entries


def _acknowledged_units() -> set[str]:
    acknowledged = set()
    for line in ALLOWLIST.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        drift_id, _, _reason = line.partition(" ")
        for prefix in ACKNOWLEDGING_DRIFT_PREFIXES:
            if drift_id.startswith(prefix):
                acknowledged.add(drift_id[len(prefix):])
    return acknowledged


def test_install_manifest_exists():
    assert MANIFEST.is_file(), (
        "cloud/config/installed-units.sha256 is missing: there is no repo-side "
        "record of which unit contents are installed on the host, so a unit "
        "edit (e.g. 0af0beab's radon-catalysts.timer reschedule) ships through "
        "CI with no signal that the separate root install step is still owed."
    )


def test_changed_units_are_acknowledged_pending_install():
    if not MANIFEST.is_file():
        import pytest

        pytest.fail(
            "no installed-units manifest -- every unit change is an "
            "unacknowledged pending install (see test_install_manifest_exists)"
        )
    installed = _manifest_hashes()
    acknowledged = _acknowledged_units()
    unacknowledged = []
    for name, path in sorted(_unit_files().items()):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if installed.get(name) == digest or name in acknowledged:
            continue
        unacknowledged.append(name)
    assert not unacknowledged, (
        "units changed in services/ without a manifest bump or drift-allowlist "
        f"acknowledgment (root install-copy still owed): {unacknowledged}"
    )
