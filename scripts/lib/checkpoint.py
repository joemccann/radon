"""Durable checkpointing for long-horizon jobs (harvesters, scrapers, ingest).

Contract (see .claude/skills/long-horizon-jobs/SKILL.md):
  <job_dir>/state.json     - cursor + completed-item hashes + error queue (atomic replace)
  <job_dir>/findings.jsonl - append-only results, fsynced per record (the WAL)
  <job_dir>/SUMMARY.md     - rolling digest, the ONLY file an agent re-reads on resume

Crash-safety ordering: a finding is appended to findings.jsonl and fsynced BEFORE
its hash is marked completed in state.json. On load the completed set is rebuilt
from the union of state.json and findings.jsonl, so a kill between the two writes
can neither duplicate a finding nor lose one.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

STATE_FILE = "state.json"
FINDINGS_FILE = "findings.jsonl"
SUMMARY_FILE = "SUMMARY.md"
SUMMARY_MAX_FINDINGS = 20
SUMMARY_MAX_ERRORS = 10
# R-073: the torn-tail repair scans the WAL backwards this many bytes at a
# time — the file can be large and must not be slurped into RAM.
TAIL_REPAIR_CHUNK_BYTES = 64 * 1024


def item_hash(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _fsync_dir(path: Path) -> None:
    """Persist a rename: os.replace is only durable once the parent
    directory's metadata is fsynced too (R-073)."""
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _atomic_write(path: Path, content: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    _fsync_dir(path.parent)


class CheckpointedJob:
    """Exactly-once item processing with kill-safe resume.

    Usage:
        job = CheckpointedJob(job_dir, "sydecar-docs")
        for item in list_items():
            if job.is_done(item.id):
                continue
            try:
                result = process(item)
                job.record_finding(item.id, result)
            except Exception as e:
                job.record_error(item.id, str(e))
            job.set_cursor({"last_listed": item.id})
        job.finish()
    """

    def __init__(self, job_dir: str | os.PathLike, job_name: str):
        self.dir = Path(job_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.name = job_name
        self._findings_fh = None
        self._load()

    # -- lifecycle ---------------------------------------------------------

    def _load(self) -> None:
        state_path = self.dir / STATE_FILE
        resumed = state_path.exists()
        self.state = self._read_state(state_path) if resumed else None
        if self.state is None:
            self.state = self._fresh_state()
        if resumed:
            self.state["stats"]["resumes"] = self.state["stats"].get("resumes", 0) + 1
        self._repair_torn_findings_tail()
        self._recover_from_findings_log()
        self.checkpoint()

    def _fresh_state(self) -> dict:
        return {
            "job": self.name,
            "version": 1,
            "started_at": _now(),
            "updated_at": _now(),
            "cursor": None,
            "completed": {},
            "errors": [],
            "stats": {"processed": 0, "failed": 0, "resumes": 0},
            "finished_at": None,
        }

    def _read_state(self, state_path: Path) -> dict | None:
        """Parsed state.json, or None after preserving a malformed file.

        A corrupt/wrong-shape state must not kill __init__ (R-073): the
        findings WAL is the source of truth for completion, so the file is
        moved aside as ``.corrupt-<ts>`` (the REL-021a daemon_state
        contract — never silently discard the forensic record) and the
        completed set is rebuilt from the WAL by ``_recover_from_findings_log``.
        """
        try:
            state = json.loads(state_path.read_text())
        except (OSError, ValueError):
            state = None
        if not self._state_is_wellformed(state):
            self._preserve_corrupt_state(state_path)
            return None
        return state

    @staticmethod
    def _state_is_wellformed(state) -> bool:
        return (
            isinstance(state, dict)
            and isinstance(state.get("completed"), dict)
            and isinstance(state.get("errors"), list)
            and isinstance(state.get("stats"), dict)
        )

    @staticmethod
    def _preserve_corrupt_state(state_path: Path) -> None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        try:
            os.replace(state_path, state_path.with_name(state_path.name + f".corrupt-{stamp}"))
        except OSError:
            pass

    def _repair_torn_findings_tail(self) -> None:
        findings_path = self.dir / FINDINGS_FILE
        if not findings_path.exists():
            return
        with findings_path.open("r+b") as findings:
            size = findings.seek(0, os.SEEK_END)
            if size == 0:
                return
            findings.seek(size - 1)
            if findings.read(1) == b"\n":
                return
            # Backwards chunked scan for the last complete line (R-073).
            position = size
            truncate_at = 0
            while position > 0:
                chunk_start = max(0, position - TAIL_REPAIR_CHUNK_BYTES)
                findings.seek(chunk_start)
                chunk = findings.read(position - chunk_start)
                newline = chunk.rfind(b"\n")
                if newline >= 0:
                    truncate_at = chunk_start + newline + 1
                    break
                position = chunk_start
            findings.truncate(truncate_at)
            findings.flush()
            os.fsync(findings.fileno())

    def _recover_from_findings_log(self) -> None:
        findings_path = self.dir / FINDINGS_FILE
        if not findings_path.exists():
            return
        with findings_path.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue  # torn final line from a kill mid-append
                key = record.get("_key")
                if not isinstance(key, str) or not key:
                    continue
                h = item_hash(key)
                if h not in self.state["completed"]:
                    self.state["completed"][h] = record.get("_at", _now())
                    self.state["stats"]["processed"] += 1

    def finish(self) -> None:
        self.state["finished_at"] = _now()
        self.checkpoint()
        if self._findings_fh:
            self._findings_fh.close()
            self._findings_fh = None

    # -- queries -----------------------------------------------------------

    def is_done(self, item_key: str) -> bool:
        return item_hash(item_key) in self.state["completed"]

    @property
    def cursor(self):
        return self.state["cursor"]

    # -- commands ----------------------------------------------------------

    def record_finding(self, item_key: str, payload: dict) -> None:
        reserved = {"_hash", "_key", "_at"}.intersection(payload)
        if reserved:
            raise ValueError(f"finding payload contains reserved keys: {sorted(reserved)}")
        h = item_hash(item_key)
        record = {**payload, "_hash": h, "_key": item_key, "_at": _now()}
        self._append_finding(record)
        self.state["completed"][h] = record["_at"]
        self.state["stats"]["processed"] += 1
        self.state["errors"] = [e for e in self.state["errors"] if e["hash"] != h]
        self.checkpoint()

    def record_error(self, item_key: str, error: str) -> None:
        h = item_hash(item_key)
        existing = next((e for e in self.state["errors"] if e["hash"] == h), None)
        if existing:
            existing["attempts"] += 1
            existing["error"] = error
            existing["last_at"] = _now()
        else:
            self.state["errors"].append(
                {"key": item_key, "hash": h, "error": error, "attempts": 1, "last_at": _now()}
            )
            self.state["stats"]["failed"] += 1
        self.checkpoint()

    def set_cursor(self, cursor) -> None:
        self.state["cursor"] = cursor

    def checkpoint(self) -> None:
        self.state["updated_at"] = _now()
        _atomic_write(self.dir / STATE_FILE, json.dumps(self.state, indent=2))
        _atomic_write(self.dir / SUMMARY_FILE, self._render_summary())

    # -- internals ---------------------------------------------------------

    def _append_finding(self, record: dict) -> None:
        if self._findings_fh is None:
            self._findings_fh = (self.dir / FINDINGS_FILE).open("a")
        self._findings_fh.write(json.dumps(record, separators=(",", ":")) + "\n")
        self._findings_fh.flush()
        os.fsync(self._findings_fh.fileno())

    def _render_summary(self) -> str:
        s = self.state
        lines = [
            f"# {s['job']} — job summary",
            "",
            f"- Started: {s['started_at']}  Updated: {s['updated_at']}",
            f"- Status: {'FINISHED ' + s['finished_at'] if s['finished_at'] else 'IN PROGRESS'}",
            f"- Processed: {s['stats']['processed']}  Failed (open): {len(s['errors'])}  Resumes: {s['stats']['resumes']}",
            f"- Cursor: `{json.dumps(s['cursor'])}`",
            "",
        ]
        if s["errors"]:
            lines.append(f"## Error queue (last {SUMMARY_MAX_ERRORS})")
            for e in s["errors"][-SUMMARY_MAX_ERRORS:]:
                lines.append(f"- `{e['key']}` x{e['attempts']}: {e['error'][:200]}")
            lines.append("")
        recent = self._tail_findings(SUMMARY_MAX_FINDINGS)
        if recent:
            lines.append(f"## Last {len(recent)} findings")
            for r in recent:
                label = r.get("title") or r.get("summary") or r.get("_key", "?")
                lines.append(f"- {r['_at']} `{r['_hash']}` {str(label)[:160]}")
            lines.append("")
        lines.append(
            "Resume: re-read ONLY this file. Full results live in findings.jsonl; "
            "machine state in state.json. Do not re-read raw transcripts or re-list completed items."
        )
        return "\n".join(lines) + "\n"

    def _tail_findings(self, n: int) -> list[dict]:
        """Last `n` findings, read from the END of the WAL.

        R-177: this slurped and JSON-parsed the ENTIRE file on every
        checkpoint — the exact pattern R-073 removed from its sibling
        `_repair_torn_findings_tail`, whose rationale (the file can be large
        and the tail is all we need) applies verbatim here. Same backwards
        chunked scan, same chunk size.
        """
        findings_path = self.dir / FINDINGS_FILE
        if not findings_path.exists():
            return []
        if n <= 0:
            return []
        with findings_path.open("rb") as findings:
            size = findings.seek(0, os.SEEK_END)
            if size == 0:
                return []
            position = size
            tail = b""
            # Read backwards until the chunk holds more than n line breaks
            # (n+1 so the first, possibly partial, line can be discarded).
            while position > 0 and tail.count(b"\n") <= n:
                chunk_start = max(0, position - TAIL_REPAIR_CHUNK_BYTES)
                findings.seek(chunk_start)
                tail = findings.read(position - chunk_start) + tail
                position = chunk_start
            complete = tail if position == 0 else tail.split(b"\n", 1)[-1]
        records: list[dict] = []
        for line in complete.split(b"\n"):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue  # torn final line from a kill mid-append
        return records[-n:]
