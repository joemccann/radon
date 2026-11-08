"""Incident JSON lifecycle on disk: open, dedup by fingerprint, resolve.

One file per incident under data/incidents/. Repeat detections of the same
fingerprint update the open file (observations, last_seen_at) instead of
spawning duplicates. An open fingerprint resolves only after
RESOLVE_AFTER_CLEAN_CYCLES consecutive cycles that definitively did not
observe it (``clean_cycles``), so a one-cycle flap keeps its file. A later
re-detection after resolution opens a fresh file so incident history stays
append-only. If two open files ever share a fingerprint, the oldest carries
the incident and the others are resolved as ``duplicate_of`` it.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .classify import CASE_PROBES

SCHEMA = "radon.incident/1"
RUNBOOK = "docs/incident-runbook.md"
PLAYBOOK = ".claude/skills/incident-response/SKILL.md"


# R-178: data/incidents/ had no retention, and `_open_incidents` re-globs and
# JSON-parses EVERY incident ever written on all 288 cycles a day (status is
# only known after the parse). The horizon is generous — an incident is a
# forensic record — but it is finite.
RESOLVED_RETENTION_DAYS = 90

# Consecutive definitive non-observations before an open incident resolves.
RESOLVE_AFTER_CLEAN_CYCLES = 2


def prune_resolved(directory: Path, *, now: datetime) -> int:
    """Delete resolved incidents older than the retention horizon.

    Fails safe in every ambiguous case: an unparseable file, an incident that
    is not resolved, and a resolved incident with no usable `resolved_at` are
    all left exactly where they are.
    """
    cutoff = now - timedelta(days=RESOLVED_RETENTION_DAYS)
    removed = 0
    for path in sorted(directory.glob("incident-*.json")):
        try:
            payload = json.loads(path.read_text())
        except (ValueError, OSError):
            continue
        if payload.get("status") != "resolved":
            continue
        raw = payload.get("resolved_at")
        if not isinstance(raw, str) or not raw:
            continue
        try:
            resolved_at = datetime.fromisoformat(raw)
        except ValueError:
            continue
        if resolved_at.tzinfo is None:
            resolved_at = resolved_at.replace(tzinfo=timezone.utc)
        if resolved_at >= cutoff:
            continue
        try:
            path.unlink()
        except OSError:
            continue
        removed += 1
    return removed


def _open_incidents(directory: Path) -> dict[str, list[Path]]:
    """Map fingerprint -> every open file for it, oldest first.

    The first path is the canonical incident; any others are duplicates.
    """
    found: dict[str, list[tuple[str, str, Path]]] = {}
    for path in sorted(directory.glob("incident-*.json")):
        try:
            payload = json.loads(path.read_text())
        except (ValueError, OSError):
            continue
        if payload.get("status") == "open":
            found.setdefault(payload.get("fingerprint", ""), []).append(
                (str(payload.get("detected_at") or ""), path.name, path)
            )
    return {fp: [path for *_key, path in sorted(rows)] for fp, rows in found.items()}


def _observed(payload: dict, incident: dict, now: datetime) -> dict:
    """open --observed--> open, clean streak reset."""
    payload["observations"] = int(payload.get("observations", 0)) + 1
    payload["last_seen_at"] = now.isoformat()
    payload["evidence"] = incident.get("evidence", payload["evidence"])
    payload["clean_cycles"] = 0
    return payload


def _not_observed(payload: dict, now: datetime) -> dict:
    """open --clean--> open (streak + 1) | resolved at the threshold."""
    clean = int(payload.get("clean_cycles", 0)) + 1
    payload["clean_cycles"] = clean
    if clean >= RESOLVE_AFTER_CLEAN_CYCLES:
        payload["status"] = "resolved"
        payload["resolved_at"] = now.isoformat()
    return payload


def _superseded(payload: dict, canonical: dict, now: datetime) -> dict:
    """open --duplicate--> resolved, pointing at the canonical incident."""
    payload["status"] = "resolved"
    payload["resolved_at"] = now.isoformat()
    payload["duplicate_of"] = canonical.get("incident_id")
    return payload


def _write(path: Path, payload: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n")
    tmp.replace(path)


def _new_payload(incident: dict, now: datetime) -> dict:
    return {
        "schema": SCHEMA,
        "incident_id": None,  # filled after the filename is derived
        "case_id": incident["case_id"],
        "severity": incident["severity"],
        "title": incident["title"],
        "status": "open",
        "detected_at": now.isoformat(),
        "last_seen_at": now.isoformat(),
        "resolved_at": None,
        "observations": 1,
        "fingerprint": incident["fingerprint"],
        "probes": list(incident.get("probes", [])),
        "evidence": incident.get("evidence", {}),
        "runbook": f"{RUNBOOK}#{incident['case_id']}",
        "playbook": PLAYBOOK,
        "next_step": "Run: /incident <path-to-this-file>",
    }


def _bearing_probes(payload: dict) -> tuple[str, ...] | None:
    probes = payload.get("probes")
    if probes:
        return tuple(probes)
    return CASE_PROBES.get(payload.get("case_id"))


def _resolution_blocked(payload: dict, indeterminate_probes: set[str]) -> bool:
    """An indeterminate probe is not evidence its own condition recovered —
    but it says nothing about incidents observed by other probes, so it must
    not latch them (R-065)."""
    probes = _bearing_probes(payload)
    if probes is None:
        return bool(indeterminate_probes)
    return any(name in indeterminate_probes for name in probes)


def record_cycle(incidents: list[dict], directory: Path | str,
                 now: datetime, *,
                 indeterminate_probes: set[str] = frozenset()) -> dict:
    """Persist one watchdog cycle. Returns {opened, updated, resolved} lists
    of file paths (as strings). ``indeterminate_probes`` names the probes
    whose state was ``unknown`` this cycle: an open incident resolves only
    when every probe bearing on it observed definitively."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    # Prune BEFORE the glob so the cycle's own scan shrinks with it (R-178).
    prune_resolved(directory, now=now)
    open_by_fingerprint = _open_incidents(directory)
    seen = {incident["fingerprint"] for incident in incidents}

    opened: list[str] = []
    updated: list[str] = []
    resolved: list[str] = []

    # Duplicates first: every open file for a fingerprint stays tracked.
    for paths in open_by_fingerprint.values():
        if len(paths) < 2:
            continue
        canonical = json.loads(paths[0].read_text())
        for extra in paths[1:]:
            _write(extra, _superseded(json.loads(extra.read_text()), canonical, now))
            resolved.append(str(extra))
        del paths[1:]

    for incident in incidents:
        existing = open_by_fingerprint.get(incident["fingerprint"])
        if existing:
            path = existing[0]
            _write(path, _observed(json.loads(path.read_text()), incident, now))
            updated.append(str(path))
            continue
        stamp = now.strftime("%Y%m%dT%H%M%SZ")
        incident_id = f"{stamp}-{incident['case_id']}"
        path = directory / f"incident-{incident_id}.json"
        suffix = 1
        while path.exists():
            suffix += 1
            path = directory / f"incident-{incident_id}-{suffix}.json"
        payload = _new_payload(incident, now)
        payload["incident_id"] = path.stem.removeprefix("incident-")
        _write(path, payload)
        opened.append(str(path))

    for fingerprint, paths in open_by_fingerprint.items():
        if fingerprint in seen:
            continue
        path = paths[0]
        payload = json.loads(path.read_text())
        # An indeterminate bearing probe is no evidence either way: the clean
        # streak neither grows nor resets.
        if _resolution_blocked(payload, indeterminate_probes):
            continue
        payload = _not_observed(payload, now)
        _write(path, payload)
        if payload["status"] == "resolved":
            resolved.append(str(path))

    return {"opened": opened, "updated": updated, "resolved": resolved}
