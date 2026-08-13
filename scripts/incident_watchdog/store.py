"""Incident JSON lifecycle on disk: open, dedup by fingerprint, resolve.

One file per incident under data/incidents/. Repeat detections of the same
fingerprint update the open file (observations, last_seen_at) instead of
spawning duplicates; a cycle that no longer observes an open fingerprint
resolves it. A later re-detection opens a fresh file so incident history
stays append-only.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

SCHEMA = "radon.incident/1"
RUNBOOK = "docs/incident-runbook.md"
PLAYBOOK = ".claude/skills/incident-response/SKILL.md"


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
        "evidence": incident.get("evidence", {}),
        "runbook": f"{RUNBOOK}#{incident['case_id']}",
        "playbook": PLAYBOOK,
        "next_step": "Run: /incident <path-to-this-file>",
    }


def record_cycle(incidents: list[dict], directory: Path | str,
                 now: datetime, *, allow_resolve: bool = True) -> dict:
    """Persist one watchdog cycle. Returns {opened, updated, resolved} lists
    of file paths (as strings)."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
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
    if not allow_resolve:
        return {"opened": opened, "updated": updated, "resolved": resolved}
    for fingerprint, path in open_by_fingerprint.items():
        if fingerprint in seen:
            continue
        payload = json.loads(path.read_text())
        payload["status"] = "resolved"
        payload["resolved_at"] = now.isoformat()
        _write(path, payload)
        resolved.append(str(path))

    return {"opened": opened, "updated": updated, "resolved": resolved}
