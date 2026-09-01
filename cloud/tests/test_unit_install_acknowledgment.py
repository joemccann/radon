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
    commit as (or immediately after) the root install-copy or the
    `sync-scheduled-units` allowlist add that will perform that copy.
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
    """Every unit artifact the host installs, keyed by its manifest name.

    T-236: this used to glob the TOP LEVEL only, which left
    services/radon-.service.d/common.conf -- the systemd prefix drop-in that
    applies RADON_DB_NO_REPLICA=1 to EVERY radon-* unit (the DUR-07
    belt-and-braces; setup-vps.sh:install_fleet_dropin installs it, and
    drift_audit.py tracks it as `fleet-dropin`) -- with no sha256 pin at all.
    Editing it silently was exactly the "repo and host diverge with nothing at
    commit time saying so" hole this module exists to close, on the one file
    whose blast radius is the whole fleet.

    Drop-in confs are keyed by their path relative to services/ (e.g.
    `radon-.service.d/common.conf`) because `common.conf` is not unique across
    drop-in directories. install-units' manifest regex only matches bare
    `radon-*.{service,timer}` names, so such a line is ignored there by design
    (see test_install_units.py's malformed-line case) -- that file is installed
    by setup-vps.sh, not by the per-release unit installer. The pin is what
    makes an edit to it visible at review time.

    `*.conf.example` is deliberately excluded: the container-cutover examples
    are not installed anywhere (cloud/tests/test_host_runtime_without_ghcr.py
    asserts neither bootstrap nor setup references them).
    """
    units = {
        p.name: p
        for p in SERVICES_DIR.iterdir()
        if p.is_file() and p.suffix in {".service", ".timer"}
    }
    for dropin in SERVICES_DIR.glob("*.service.d/*.conf"):
        if dropin.is_file():
            units[dropin.relative_to(SERVICES_DIR).as_posix()] = dropin
    return units


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


# --- REL-045 (R-092, R-113): the `not-installed:` ack is a trapdoor.
#
# R-092: radon-ivrank.{service,timer} took the ack path instead of a manifest
# bump. After eb5cc11f the automated installer iterates the MANIFEST, and
# `sync-scheduled-units` requires a manifest entry too, so an ack-only unit is
# excluded from every automated install path while the ack keeps config-drift
# green for four months. `_check_stale` treats a service with no
# `service_health` row as dormant-healthy, so the watchdog stays silent and
# `/api/ivrank` serves `missing: true` forever. The IV RANK tab shipped
# "live-verified" on a job that never ran in production.
#
# R-113: radon-credit-spread.{service,timer} were manifest-pinned AND still
# carried acks. The first successful install-units makes `partition_allowlisted`
# emit `stale-allowlist:` for both, turning config-drift error until two comment
# lines are deleted. The REL-032 ratchet firing on a self-inflicted condition.


def _not_installed_acks() -> dict[str, str]:
    """`not-installed:<unit>` -> its reason text."""
    acks: dict[str, str] = {}
    for line in ALLOWLIST.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        drift_id, _, reason = line.partition(" ")
        if drift_id.startswith("not-installed:"):
            acks[drift_id[len("not-installed:"):]] = reason.strip()
    return acks


def test_a_not_installed_ack_never_coexists_with_a_manifest_entry():
    """R-113: the two say opposite things, and once install-units succeeds
    the ack becomes a `stale-allowlist:` drift that pages daily."""
    installed = set(_manifest_hashes())
    both = sorted(set(_not_installed_acks()) & installed)
    assert not both, (
        "these units are manifest-pinned AND carry a not-installed ack, so the "
        "first successful install-units turns config-drift error via "
        f"stale-allowlist: {both}"
    )


def test_a_not_installed_ack_names_a_gate_the_installer_cannot_clear():
    """R-092: "root install-copy owed after merge" is no longer a real gate —
    the automated installer performs exactly that copy, from the manifest. An
    ack whose reason is a pending install is a unit that will NEVER install,
    silently. A legitimate ack names something outside the deploy's control.
    """
    install_owed_phrases = (
        "install-copy",
        "install copy",
        "owed",
        "pending install",
        "next vps",
        "reinstall",
    )
    offenders = []
    for unit, reason in sorted(_not_installed_acks().items()):
        lowered = reason.lower()
        if any(phrase in lowered for phrase in install_owed_phrases):
            offenders.append(f"{unit}: {reason}")
    assert not offenders, (
        "a not-installed ack may not be used for work the automated installer "
        "now does — add the unit to config/installed-units.sha256 instead. "
        f"Offenders: {offenders}"
    )


def test_every_unit_is_either_manifest_pinned_or_acked_for_a_real_reason():
    """No unit may fall through both paths into silent non-installation."""
    installed = set(_manifest_hashes())
    acked = set(_not_installed_acks()) | _acknowledged_units()
    orphans = sorted(set(_unit_files()) - installed - acked)
    assert not orphans, (
        "units in services/ with neither a manifest entry nor an ack — they "
        f"install nowhere and nothing reports it: {orphans}"
    )


def test_the_fleet_wide_prefix_dropin_is_pinned():
    """T-236: name the one file this guard used to miss, so a future refactor
    of `_unit_files()` that narrows the glob back to the top level reds here
    rather than silently unpinning the fleet.

    `radon-.service.d/common.conf` is a systemd PREFIX drop-in: systemd applies
    it to every unit named `radon-*`, so an unreviewed edit reaches radon-api,
    radon-monitor, radon-nextjs and every timer oneshot at once.
    """
    name = "radon-.service.d/common.conf"
    units = _unit_files()
    assert name in units, (
        f"{name} is not covered by the manifest guard -- the fleet-wide "
        "drop-in can be edited with no sha256 pin"
    )
    digest = hashlib.sha256(units[name].read_bytes()).hexdigest()
    assert _manifest_hashes().get(name) == digest, (
        f"{name} content does not match its config/installed-units.sha256 "
        "entry; bump it in the same commit as the setup-vps install_fleet_dropin "
        "copy that puts it on the host"
    )
