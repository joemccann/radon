#!/usr/bin/env python3
"""DeepSec sibling worker: status, harvest, last-audited engines, queue lock.

DeepSec is a long-running sibling of the security nightly, not a second
audit -> remediate -> deliver loop. It writes verified findings into the
shared private queue under ``~/radon-weekend/.security-nightly-scratch``.
The security audit harvests whatever export is already ready and does not
wait on DeepSec's wall clock.

Stdlib only. The wrappers pipe the origin/main copy into
``/usr/bin/python3 -I -`` after the agent; this file must not import
anything from the clone.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional, Sequence, Tuple

FAST_ENGINES: Tuple[str, ...] = ("gitleaks", "deterministic", "claude_security")
DEEPSEC_ENGINE = "deepsec"
FAST_ENGINES_MARKER = "fast-engines.complete"
STATUS_NAME = "deepsec-status.json"
LAST_AUDITED_NAME = "last-audited.json"
QUEUE_NAME = "findings.jsonl"
HARVESTED_NAME = "harvested.json"
LOCK_DIRNAME = "queue.lock"
EXPORT_BASENAME = "deepsec-verified-findings.json"

STATUSES = ("running", "failed", "export_ready", "harvested")
PUBLIC_STATUS = {
    "running": "still running",
    "failed": "failed",
    "export_ready": "export ready",
    "harvested": "harvested",
}
AUDIT_OK_DEEPSEC_RUNNING = "OK (fast engines complete; DeepSec still running)"


def scratch_root(weekend_root: Path) -> Path:
    return Path(weekend_root) / ".security-nightly-scratch"


def queue_dir(scratch: Path) -> Path:
    path = Path(scratch) / "queue"
    path.mkdir(parents=True, exist_ok=True)
    return path


def last_audited_path(scratch: Path) -> Path:
    return Path(scratch) / LAST_AUDITED_NAME


def status_path(scratch: Path) -> Path:
    return Path(scratch) / STATUS_NAME


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _read_json(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def load_last_audited(scratch: Path) -> Dict[str, Any]:
    data = _read_json(last_audited_path(scratch))
    engines = data.get("engines")
    if not isinstance(engines, dict):
        engines = {}
        data["engines"] = engines
    return data


def save_last_audited(scratch: Path, data: Mapping[str, Any]) -> None:
    _atomic_write(last_audited_path(scratch), json.dumps(data, indent=2, sort_keys=True) + "\n")


def engine_sha(data: Mapping[str, Any], engine: str) -> Optional[str]:
    engines = data.get("engines")
    if isinstance(engines, dict) and engines.get(engine):
        return str(engines[engine])
    value = data.get(engine)
    return str(value) if value else None


def advance_engines(
    scratch: Path, engines: Sequence[str], sha: str
) -> Dict[str, Any]:
    state = load_last_audited(scratch)
    bucket = state.setdefault("engines", {})
    if not isinstance(bucket, dict):
        bucket = {}
        state["engines"] = bucket
    for name in engines:
        bucket[name] = sha
        if name in state and name != "engines":
            state[name] = sha
    save_last_audited(scratch, state)
    return state


def load_status(scratch: Path) -> Dict[str, Any]:
    return _read_json(status_path(scratch))


def save_status(scratch: Path, payload: Mapping[str, Any]) -> Path:
    path = status_path(scratch)
    _atomic_write(path, json.dumps(dict(payload), indent=2, sort_keys=True) + "\n")
    return path


def public_status(internal: str) -> str:
    return PUBLIC_STATUS.get(internal, "failed")


def public_status_line(scratch: Path) -> str:
    status = str(load_status(scratch).get("status") or "failed")
    return f"DEEPSEC {public_status(status)}"


def _pid_alive(pid: Any) -> bool:
    try:
        n = int(pid)
    except (TypeError, ValueError):
        return False
    if n <= 0:
        return False
    try:
        os.kill(n, 0)
    except OSError:
        return False
    return True


def _run_dirs(scratch: Path) -> List[Path]:
    root = Path(scratch)
    if not root.is_dir():
        return []
    out = []
    for child in root.iterdir():
        if child.is_dir() and child.name not in {"queue", "deepsec-worker"}:
            out.append(child)
    return sorted(out, key=lambda p: p.name, reverse=True)


def _completion_marker_matches(path: Path, head_sha: str) -> bool:
    """Accept only a completion marker written for this exact audit head."""
    if not head_sha or not path.is_file():
        return False
    try:
        return path.read_text(encoding="utf-8").strip() == head_sha
    except OSError:
        return False


def _run_record_matches(path: Path, head_sha: str) -> bool:
    """Require both the completion state and exact head in legacy run records."""
    if not head_sha or not path.is_file():
        return False
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    return bool(
        re.search(r"(?m)^fast_engines:\s*complete\s*$", text)
        and re.search(rf"(?m)^head_sha:\s*{re.escape(head_sha)}\s*$", text)
    )


def fast_engines_complete(scratch: Path, head_sha: str = "") -> bool:
    marker = Path(scratch) / FAST_ENGINES_MARKER
    if _completion_marker_matches(marker, head_sha):
        return True
    for run in _run_dirs(scratch):
        if _completion_marker_matches(run / FAST_ENGINES_MARKER, head_sha):
            return True
        record = run / "run-record.md"
        if _run_record_matches(record, head_sha):
            return True
    if not head_sha:
        return False
    state = load_last_audited(scratch)
    return all(engine_sha(state, name) == head_sha for name in FAST_ENGINES)


def classify_audit(
    *,
    rc: int,
    scratch: Path,
    head_sha: str = "",
    cap_secs: int = 7200,
) -> Tuple[str, int]:
    if rc != 124:
        if rc == 0:
            return "OK", 0
        return f"FAILED (exit {rc})", rc
    status = str(load_status(scratch).get("status") or "")
    if status == "running" and fast_engines_complete(scratch, head_sha):
        # The sibling worker (or its resume) owns DeepSec. A 2h audit cap
        # must not rewrite that as TIMEOUT when the fast engines finished.
        return AUDIT_OK_DEEPSEC_RUNNING, 0
    return f"TIMEOUT after {cap_secs}s", 124


class QueueLock:
    """mkdir lock shared by harvest and remediate. macOS has no flock(1)."""

    def __init__(self, scratch: Path) -> None:
        self.path = queue_dir(scratch) / LOCK_DIRNAME

    def acquire(self, timeout_secs: float = 30.0) -> None:
        deadline = time.time() + timeout_secs
        while True:
            try:
                os.mkdir(self.path)
                (self.path / "pid").write_text(f"{os.getpid()}\n", encoding="utf-8")
                return
            except FileExistsError:
                held = ""
                try:
                    held = (self.path / "pid").read_text(encoding="utf-8").strip()
                except OSError:
                    held = ""
                if held and not _pid_alive(held):
                    try:
                        os.remove(self.path / "pid")
                    except OSError:
                        pass
                    try:
                        os.rmdir(self.path)
                    except OSError:
                        pass
                    continue
                if time.time() >= deadline:
                    raise TimeoutError("queue lock not acquired")
                time.sleep(0.05)

    def release(self) -> None:
        if not self.path.is_dir():
            return
        try:
            held = (self.path / "pid").read_text(encoding="utf-8").strip()
        except OSError:
            held = ""
        if held and held != str(os.getpid()):
            return
        try:
            os.remove(self.path / "pid")
        except OSError:
            pass
        try:
            os.rmdir(self.path)
        except OSError:
            pass

    def __enter__(self) -> "QueueLock":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()


@contextmanager
def queue_locked(scratch: Path, timeout_secs: float = 30.0) -> Iterator[QueueLock]:
    lock = QueueLock(scratch)
    lock.acquire(timeout_secs=timeout_secs)
    try:
        yield lock
    finally:
        lock.release()


def load_queue(scratch: Path) -> List[Dict[str, Any]]:
    path = queue_dir(scratch) / QUEUE_NAME
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def replace_queue(scratch: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path = queue_dir(scratch) / QUEUE_NAME
    text = "".join(json.dumps(dict(row), sort_keys=True) + "\n" for row in rows)
    _atomic_write(path, text)


def _harvested_state(scratch: Path) -> Dict[str, Any]:
    data = _read_json(queue_dir(scratch) / HARVESTED_NAME)
    ids = data.get("ids")
    exports = data.get("exports")
    if not isinstance(ids, list):
        ids = []
    if not isinstance(exports, list):
        exports = []
    return {"ids": [str(x) for x in ids], "exports": [str(x) for x in exports]}


def _save_harvested(scratch: Path, state: Mapping[str, Any]) -> None:
    _atomic_write(
        queue_dir(scratch) / HARVESTED_NAME,
        json.dumps(dict(state), indent=2, sort_keys=True) + "\n",
    )


def _finding_id(row: Mapping[str, Any]) -> str:
    for key in ("id", "finding_id", "uid"):
        value = row.get(key)
        if value:
            return str(value)
    blob = json.dumps(dict(row), sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _iter_findings(payload: Any) -> Iterable[Dict[str, Any]]:
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        rows = payload.get("findings") or payload.get("items") or []
        if isinstance(rows, dict):
            rows = list(rows.values())
    else:
        rows = []
    for row in rows:
        if isinstance(row, dict):
            yield row


def _resolve_export(scratch: Path, status: Mapping[str, Any]) -> Optional[Path]:
    raw = status.get("export_path")
    if raw:
        path = Path(str(raw))
        if path.is_file():
            return path
    run_id = status.get("run_id")
    if run_id:
        candidate = Path(scratch) / str(run_id) / EXPORT_BASENAME
        if candidate.is_file():
            return candidate
    for run in _run_dirs(scratch):
        candidate = run / EXPORT_BASENAME
        if candidate.is_file():
            return candidate
    return None


class HarvestResult:
    def __init__(self, status: str, harvested: int = 0, skipped: int = 0) -> None:
        self.status = status
        self.harvested = harvested
        self.skipped = skipped

    def public_line(self) -> str:
        return (
            f"harvested={self.harvested} skipped={self.skipped} "
            f"status={self.status.replace(' ', '_')}"
        )


def harvest(scratch: Path, timeout_secs: float = 30.0) -> HarvestResult:
    status = load_status(scratch)
    internal = str(status.get("status") or "")
    if internal == "running":
        return HarvestResult("still running")
    if internal == "failed":
        return HarvestResult("failed")
    if internal not in {"export_ready", "harvested"}:
        return HarvestResult(public_status(internal) if internal else "failed")
    export = _resolve_export(scratch, status)
    if export is None:
        if internal == "harvested":
            return HarvestResult("harvested", harvested=0, skipped=0)
        return HarvestResult("failed")
    try:
        payload = json.loads(export.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return HarvestResult("failed")

    lock = QueueLock(scratch)
    lock.acquire(timeout_secs=timeout_secs)
    try:
        seen = _harvested_state(scratch)
        known = set(seen["ids"])
        queued = load_queue(scratch)
        added = 0
        skipped = 0
        for row in _iter_findings(payload):
            fid = _finding_id(row)
            if fid in known:
                skipped += 1
                continue
            queued.append({"id": fid, "engine": DEEPSEC_ENGINE, "verified": True})
            known.add(fid)
            added += 1
        replace_queue(scratch, queued)
        export_key = str(export)
        if export_key not in seen["exports"]:
            seen["exports"].append(export_key)
        seen["ids"] = sorted(known)
        _save_harvested(scratch, seen)
        head = str(status.get("head_sha") or "")
        if head:
            advance_engines(scratch, (DEEPSEC_ENGINE,), head)
            last = load_last_audited(scratch)
            last[DEEPSEC_ENGINE] = head
            save_last_audited(scratch, last)
        save_status(scratch, {**status, "status": "harvested"})
        return HarvestResult("harvested", harvested=added, skipped=skipped)
    finally:
        lock.release()


def _cmd_classify_audit(args: argparse.Namespace) -> int:
    status, rc = classify_audit(
        rc=args.rc,
        scratch=Path(args.scratch),
        head_sha=args.head_sha or "",
        cap_secs=args.cap_secs,
    )
    sys.stdout.write(f"{status}\t{rc}\n")
    return 0


def _cmd_harvest(args: argparse.Namespace) -> int:
    result = harvest(Path(args.scratch), timeout_secs=args.timeout)
    sys.stdout.write(result.public_line() + "\n")
    return 0 if result.status in {"harvested", "still running"} else 1


def _cmd_status(args: argparse.Namespace) -> int:
    sys.stdout.write(public_status_line(Path(args.scratch)) + "\n")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="DeepSec sibling harvest and status.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    classify = sub.add_parser("classify-audit")
    classify.add_argument("--rc", type=int, required=True)
    classify.add_argument("--scratch", required=True)
    classify.add_argument("--head-sha", default="")
    classify.add_argument("--cap-secs", type=int, default=7200)
    classify.set_defaults(func=_cmd_classify_audit)

    harvest_p = sub.add_parser("harvest")
    harvest_p.add_argument("--scratch", required=True)
    harvest_p.add_argument("--timeout", type=float, default=30.0)
    harvest_p.set_defaults(func=_cmd_harvest)

    status_p = sub.add_parser("status")
    status_p.add_argument("--scratch", required=True)
    status_p.set_defaults(func=_cmd_status)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
