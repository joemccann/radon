"""Laptop-side incident responder — auto-triage, human-gated ship.

Every cycle (launchd com.radon.incident-responder, 10 min): mirror the VPS
incident directory, pick open incidents not yet analyzed, and run the
/incident playbook in ANALYZE-ONLY mode via headless Claude Code. The
diagnosis lands next to the mirrored incident as <incident_id>.diagnosis.md
and a macOS notification fires. Shipping a fix stays a human decision —
this never pushes code.

Stdlib only. State in data/incidents_remote/.responder-state.json; a pid
lockfile prevents overlapping cycles (an analysis can outlive the 10-min
interval).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_REMOTE = "radon@ib-gateway"
DEFAULT_REMOTE_DIR = "/home/radon/radon/data/incidents"
DEFAULT_LOCAL_DIR = Path("data/incidents_remote")
ANALYZE_LIMIT_PER_CYCLE = 2
ANALYZE_TIMEOUT_SECS = 2400
LOCK_STALE_SECS = 3 * 3600
# Self-resolving transients (mid-deploy marker mismatch, writers erroring
# through the deploy restart window) auto-resolve within a watchdog cycle
# or two — never spend a headless-Claude run on an incident younger than
# two watchdog cycles + margin.
MIN_INCIDENT_AGE_SECS = 12 * 60


def load_state(state_path: Path) -> dict:
    try:
        return json.loads(state_path.read_text())
    except (OSError, ValueError):
        return {"analyzed": {}}


def mark_analyzed(state_path: Path, state: dict, incident_id: str,
                  now: datetime) -> None:
    state["analyzed"][incident_id] = now.isoformat()
    state_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = state_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2) + "\n")
    tmp.replace(state_path)


def _is_past_transient_window(payload: dict, now: datetime) -> bool:
    raw = payload.get("detected_at")
    if not raw:
        return True
    try:
        detected = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return True
    if detected.tzinfo is None:
        detected = detected.replace(tzinfo=timezone.utc)
    return (now - detected).total_seconds() >= MIN_INCIDENT_AGE_SECS


def select_new_open_incidents(mirror_dir: Path, state: dict,
                              limit: int = ANALYZE_LIMIT_PER_CYCLE,
                              now: datetime | None = None) -> list[Path]:
    now = now or datetime.now(timezone.utc)
    analyzed = state.get("analyzed", {})
    selected = []
    for path in sorted(mirror_dir.glob("incident-*.json")):
        try:
            payload = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if payload.get("status") != "open":
            continue
        if payload.get("incident_id") in analyzed:
            continue
        if not _is_past_transient_window(payload, now):
            continue
        selected.append(path)
    return selected[:limit]


def build_sync_command(remote: str, remote_dir: str, local_dir: Path) -> list[str]:
    # The dedup state, lock, and diagnoses live beside the mirrored incident
    # files. Excluding them protects them from --delete (rsync never deletes
    # excluded paths without --delete-excluded); without this every cycle
    # wiped the state and re-analyzed the same incident.
    return [
        "rsync", "-az", "--delete", "--timeout=30",
        "--exclude=.responder-state.json",
        "--exclude=.responder.lock",
        "--exclude=*.diagnosis.md",
        f"{remote}:{remote_dir}/",
        f"{local_dir}/",
    ]


def build_analyze_command(incident_path: Path) -> list[str]:
    prompt = f"/incident {incident_path} --analyze-only"
    return [
        "claude", "-p", prompt,
        "--permission-mode", "acceptEdits",
        "--allowedTools", "Read", "Grep", "Glob", "Bash", "Agent", "WebFetch",
    ]


def notify(title: str, message: str) -> None:
    script = f'display notification "{message}" with title "{title}"'
    subprocess.run(["osascript", "-e", script], capture_output=True, timeout=10)


def acquire_lock(lock_path: Path, now: datetime) -> bool:
    if lock_path.exists():
        age = now.timestamp() - lock_path.stat().st_mtime
        if age < LOCK_STALE_SECS:
            return False
        lock_path.unlink(missing_ok=True)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(str(os.getpid()))
    return True


def analyze(incident_path: Path, repo_root: Path) -> str:
    try:
        proc = subprocess.run(
            build_analyze_command(incident_path),
            cwd=repo_root, capture_output=True, text=True,
            timeout=ANALYZE_TIMEOUT_SECS,
        )
    except subprocess.TimeoutExpired:
        return (
            f"ANALYSIS TIMED OUT after {ANALYZE_TIMEOUT_SECS}s — "
            "recorded so the next cycle does not re-launch the same hung run. "
            "Re-triage manually with: /incident " + str(incident_path)
        )
    if proc.returncode != 0:
        return f"ANALYSIS FAILED (exit {proc.returncode})\n\n{proc.stderr[-4000:]}"
    return proc.stdout


def run_cycle(repo_root: Path) -> int:
    remote = os.environ.get("INCIDENT_RESPONDER_REMOTE", DEFAULT_REMOTE)
    remote_dir = os.environ.get("INCIDENT_RESPONDER_REMOTE_DIR", DEFAULT_REMOTE_DIR)
    mirror = repo_root / os.environ.get(
        "INCIDENT_RESPONDER_LOCAL_DIR", str(DEFAULT_LOCAL_DIR))
    now = datetime.now(timezone.utc)

    lock = mirror / ".responder.lock"
    if not acquire_lock(lock, now):
        print("previous cycle still running; skipping", file=sys.stderr)
        return 0
    try:
        mirror.mkdir(parents=True, exist_ok=True)
        sync = subprocess.run(build_sync_command(remote, remote_dir, mirror),
                              capture_output=True, text=True, timeout=60)
        if sync.returncode != 0:
            print(f"rsync failed: {sync.stderr.strip()}", file=sys.stderr)
            return 1

        state_path = mirror / ".responder-state.json"
        state = load_state(state_path)
        incidents = select_new_open_incidents(mirror, state, now=now)
        if not incidents:
            print(json.dumps({"at": now.isoformat(), "new_incidents": 0}))
            return 0

        for path in incidents:
            payload = json.loads(path.read_text())
            incident_id = payload["incident_id"]
            notify("Radon incident", f"Analyzing {payload['severity']} {payload['case_id']}")
            diagnosis = analyze(path, repo_root)
            (mirror / f"{incident_id}.diagnosis.md").write_text(diagnosis)
            mark_analyzed(state_path, state, incident_id, datetime.now(timezone.utc))
            notify(
                "Radon incident diagnosed",
                f"{payload['severity']} {payload['case_id']} — see {incident_id}.diagnosis.md",
            )
            print(json.dumps({
                "at": now.isoformat(),
                "analyzed": incident_id,
                "diagnosis": f"{incident_id}.diagnosis.md",
            }))
        return 0
    finally:
        lock.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(run_cycle(Path(__file__).resolve().parent.parent))
