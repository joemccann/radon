"""Checkpoint contract tests: exactly-once resume across kills (scripts/lib/checkpoint.py)."""

import json
import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

from checkpoint import CheckpointedJob, item_hash  # noqa: E402


@pytest.fixture
def job_dir(tmp_path):
    return tmp_path / "job"


def read_findings(job_dir):
    path = job_dir / "findings.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_fresh_job_writes_all_three_files(job_dir):
    job = CheckpointedJob(job_dir, "t")
    job.record_finding("item-1", {"title": "one"})
    job.finish()
    assert (job_dir / "state.json").exists()
    assert (job_dir / "findings.jsonl").exists()
    assert (job_dir / "SUMMARY.md").exists()


def test_is_done_after_record(job_dir):
    job = CheckpointedJob(job_dir, "t")
    assert not job.is_done("item-1")
    job.record_finding("item-1", {"title": "one"})
    assert job.is_done("item-1")


def test_resume_skips_completed_items(job_dir):
    job = CheckpointedJob(job_dir, "t")
    job.record_finding("item-1", {"title": "one"})
    job.finish()

    resumed = CheckpointedJob(job_dir, "t")
    assert resumed.is_done("item-1")
    assert not resumed.is_done("item-2")
    assert resumed.state["stats"]["resumes"] == 1


def test_finding_logged_but_not_marked_is_recovered_on_load(job_dir):
    """Kill between findings.jsonl append and state.json write must not duplicate."""
    job = CheckpointedJob(job_dir, "t")
    job.record_finding("item-1", {"title": "one"})
    # Simulate the crash window: erase the completed mark, keep the WAL line.
    state = json.loads((job_dir / "state.json").read_text())
    state["completed"] = {}
    state["stats"]["processed"] = 0
    (job_dir / "state.json").write_text(json.dumps(state))

    resumed = CheckpointedJob(job_dir, "t")
    assert resumed.is_done("item-1")
    assert resumed.state["stats"]["processed"] == 1


def test_torn_final_jsonl_line_is_ignored(job_dir):
    job = CheckpointedJob(job_dir, "t")
    job.record_finding("item-1", {"title": "one"})
    with (job_dir / "findings.jsonl").open("a") as f:
        f.write('{"_hash": "deadbeefdeadbeef", "_key": "item-2", "tru')

    resumed = CheckpointedJob(job_dir, "t")
    assert resumed.is_done("item-1")
    assert not resumed.is_done("item-2")


def test_resume_truncates_torn_wal_tail_before_append(job_dir):
    job = CheckpointedJob(job_dir, "t")
    job.record_finding("item-1", {"title": "one"})
    job.finish()
    with (job_dir / "findings.jsonl").open("ab") as findings:
        findings.write(b'{"_key":"torn"')

    resumed = CheckpointedJob(job_dir, "t")
    resumed.record_finding("item-2", {"title": "two"})
    resumed.finish()

    records = read_findings(job_dir)
    assert [record["_key"] for record in records] == ["item-1", "item-2"]


@pytest.mark.parametrize("reserved", ["_hash", "_key", "_at"])
def test_payload_cannot_forge_reserved_metadata_or_resume_hash(job_dir, reserved):
    job = CheckpointedJob(job_dir, "t")
    with pytest.raises(ValueError, match="reserved"):
        job.record_finding("item-1", {reserved: "forged"})


def test_recovery_recomputes_hash_from_validated_key(job_dir):
    job = CheckpointedJob(job_dir, "t")
    job.finish()
    forged = {"_key": "item-1", "_hash": item_hash("item-2"), "_at": "now"}
    (job_dir / "findings.jsonl").write_text(json.dumps(forged) + "\n")

    resumed = CheckpointedJob(job_dir, "t")

    assert resumed.is_done("item-1")
    assert not resumed.is_done("item-2")


def test_error_queue_tracks_attempts_and_clears_on_success(job_dir):
    job = CheckpointedJob(job_dir, "t")
    job.record_error("item-1", "boom")
    job.record_error("item-1", "boom again")
    assert job.state["errors"][0]["attempts"] == 2
    job.record_finding("item-1", {"title": "recovered"})
    assert job.state["errors"] == []


def test_cursor_survives_resume(job_dir):
    job = CheckpointedJob(job_dir, "t")
    job.set_cursor({"page": 7})
    job.checkpoint()
    resumed = CheckpointedJob(job_dir, "t")
    assert resumed.cursor == {"page": 7}


def test_summary_contains_counts_cursor_and_resume_instruction(job_dir):
    job = CheckpointedJob(job_dir, "t")
    job.set_cursor({"page": 3})
    job.record_finding("item-1", {"title": "alpha"})
    job.record_error("item-2", "http 500")
    summary = (job_dir / "SUMMARY.md").read_text()
    assert "Processed: 1" in summary
    assert '"page": 3' in summary
    assert "http 500" in summary
    assert "re-read ONLY this file" in summary


# ── R-073: durability hardening ──────────────────────────────────────


def test_atomic_write_fsyncs_the_parent_directory(job_dir, monkeypatch):
    """os.replace alone leaves the rename in the un-fsynced directory
    metadata — a power cut can lose the whole checkpoint file."""
    import stat as stat_mod

    import checkpoint as cp

    job_dir.mkdir(parents=True)
    synced_dir_fds = []
    real_fsync = os.fsync

    def recording_fsync(fd):
        try:
            if stat_mod.S_ISDIR(os.fstat(fd).st_mode):
                synced_dir_fds.append(fd)
        except OSError:
            pass
        return real_fsync(fd)

    monkeypatch.setattr(cp.os, "fsync", recording_fsync)
    cp._atomic_write(job_dir / "state.json", "{}")
    assert synced_dir_fds, "os.replace was not followed by a parent-dir fsync"


def test_malformed_state_json_rebuilds_from_wal_instead_of_killing_init(job_dir):
    """REL-021a's daemon_state contract: corrupt state is preserved as
    .corrupt-<ts> and the job resumes from the findings WAL."""
    job = CheckpointedJob(job_dir, "t")
    job.record_finding("item-1", {"title": "one"})
    job.finish()
    (job_dir / "state.json").write_text('{"job": "t", "completed": {"trunc')

    resumed = CheckpointedJob(job_dir, "t")

    assert resumed.is_done("item-1")
    assert not resumed.is_done("item-2")
    assert resumed.state["stats"]["processed"] == 1
    backups = list(job_dir.glob("state.json.corrupt-*"))
    assert len(backups) == 1
    assert backups[0].read_text().startswith('{"job": "t"')


def test_wrong_shape_state_json_is_treated_as_corrupt(job_dir):
    """Valid JSON without the required keys must not KeyError later."""
    job = CheckpointedJob(job_dir, "t")
    job.record_finding("item-1", {"title": "one"})
    job.finish()
    (job_dir / "state.json").write_text('["not", "a", "state", "object"]')

    resumed = CheckpointedJob(job_dir, "t")
    assert resumed.is_done("item-1")
    assert list(job_dir.glob("state.json.corrupt-*"))


def test_torn_tail_repair_streams_in_bounded_chunks(job_dir):
    """The WAL can be large; the tail repair must scan backwards in bounded
    chunks, never slurp the whole file into RAM."""
    import checkpoint as cp

    assert hasattr(cp, "TAIL_REPAIR_CHUNK_BYTES")
    assert 0 < cp.TAIL_REPAIR_CHUNK_BYTES <= 1024 * 1024


def test_torn_tail_longer_than_one_chunk_is_still_repaired(job_dir, monkeypatch):
    import checkpoint as cp

    monkeypatch.setattr(cp, "TAIL_REPAIR_CHUNK_BYTES", 64, raising=False)
    job = CheckpointedJob(job_dir, "t")
    job.record_finding("item-1", {"title": "one"})
    job.finish()
    with (job_dir / "findings.jsonl").open("ab") as findings:
        findings.write(b'{"_key":"torn","junk":"' + b"x" * 500)

    resumed = CheckpointedJob(job_dir, "t")
    resumed.record_finding("item-2", {"title": "two"})
    resumed.finish()

    records = read_findings(job_dir)
    assert [record["_key"] for record in records] == ["item-1", "item-2"]


KILL_WORKER = """
import sys, time, pathlib
sys.path.insert(0, {libdir!r})
from checkpoint import CheckpointedJob
STOP_AFTER = {stop_after}
SENTINEL = pathlib.Path({sentinel!r})
job = CheckpointedJob({jobdir!r}, "kill-test")
written = 0
for i in range(200):
    key = f"item-{{i:03d}}"
    if job.is_done(key):
        continue
    job.record_finding(key, {{"title": f"finding {{i}}"}})
    job.set_cursor({{"i": i}})
    written += 1
    if STOP_AFTER and written >= STOP_AFTER:
        SENTINEL.write_text(str(i))
        # Park so the SIGKILL lands mid-run by construction. A fixed sleep in
        # the parent raced CPython startup: on a cold runner every kill landed
        # before the first finding and the test silently stopped testing resume.
        time.sleep(30)
job.finish()
print("DONE")
"""


def _write_worker(script, tmp_path, job_dir, stop_after, tag):
    libdir = str(Path(__file__).resolve().parents[1] / "lib")
    sentinel = tmp_path / f"sentinel-{tag}"
    script.write_text(
        KILL_WORKER.format(
            libdir=libdir,
            jobdir=str(job_dir),
            stop_after=stop_after,
            sentinel=str(sentinel),
        )
    )
    return sentinel


def test_sigkill_mid_run_resumes_exactly_once(job_dir, tmp_path):
    import time

    script = tmp_path / "worker.py"

    for stop_after in (5, 20, 60):
        sentinel = _write_worker(script, tmp_path, job_dir, stop_after, stop_after)
        proc = subprocess.Popen([sys.executable, str(script)])
        deadline = time.monotonic() + 30.0
        while not sentinel.exists() and time.monotonic() < deadline:
            if proc.poll() is not None:
                break
            time.sleep(0.01)
        if not sentinel.exists():
            if proc.poll() is None:
                os.kill(proc.pid, signal.SIGKILL)
            proc.wait()
            pytest.fail(
                f"worker never recorded {stop_after} findings; the SIGKILL would "
                "be a no-op and the resume path would go untested"
            )
        os.kill(proc.pid, signal.SIGKILL)
        proc.wait()

        partial = read_findings(job_dir)
        assert 0 < len(partial) < 200, (
            f"kill after {stop_after} findings left {len(partial)} on disk - "
            "the run must be interrupted mid-flight, not before or after it"
        )

    _write_worker(script, tmp_path, job_dir, 0, "final")
    final = subprocess.run(
        [sys.executable, str(script)], capture_output=True, text=True, check=True
    )
    assert "DONE" in final.stdout

    findings = read_findings(job_dir)
    hashes = [f["_hash"] for f in findings]
    assert len(hashes) == len(set(hashes)), "duplicate findings after kills"
    assert sorted(f["_key"] for f in findings) == [f"item-{i:03d}" for i in range(200)]
    state = json.loads((job_dir / "state.json").read_text())
    assert state["stats"]["processed"] == 200
    assert set(state["completed"]) == {item_hash(f"item-{i:03d}") for i in range(200)}
