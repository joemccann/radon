"""Incident JSON lifecycle on disk: open, dedup by fingerprint, resolve.

One file per incident under data/incidents/. Repeat detections of the same
fingerprint update the open file (observations, last_seen_at) instead of
spawning duplicates; a cycle that no longer observes an open fingerprint
resolves it. A later re-detection opens a fresh file so incident history
stays append-only.
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


def _open_incidents(directory: Path) -> dict[str, Path]:
    """Map fingerprint -> file path for every open incident on disk."""
    open_by_fingerprint: dict[str, Path] = {}
    for path in sorted(directory.glob("incident-*.json")):
        try:
            payload = json.loads(path.read_text())
        except (ValueError, OSError):
            continue
        if payload.get("status") == "open":
            open_by_fingerprint[payload.get("fingerprint", "")] = path
    return open_by_fingerprint


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
    for incident in incidents:
        existing = open_by_fingerprint.get(incident["fingerprint"])
        if existing:
            payload = json.loads(existing.read_text())
            payload["observations"] = int(payload.get("observations", 0)) + 1
            payload["last_seen_at"] = now.isoformat()
            payload["evidence"] = incident.get("evidence", payload["evidence"])
            _write(existing, payload)
            updated.append(str(existing))
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

    resolved: list[str] = []
    for fingerprint, path in open_by_fingerprint.items():
        if fingerprint in seen:
            continue
        payload = json.loads(path.read_text())
        if _resolution_blocked(payload, indeterminate_probes):
            continue
        payload["status"] = "resolved"
        payload["resolved_at"] = now.isoformat()
        _write(path, payload)
        resolved.append(str(path))

    return {"opened": opened, "updated": updated, "resolved": resolved}
