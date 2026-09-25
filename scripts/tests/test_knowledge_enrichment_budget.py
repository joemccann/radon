"""Optional enrichment must never consume the hourly ingestion window."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from knowledge import distill as subject


def test_failed_wave_opens_run_breaker_and_defers_later_sources():
    calls = []
    def runner(docs, timeout):
        calls.append(len(docs))
        return [None] * len(docs)
    budget = subject.EnrichmentBudget(runner=runner)
    results, deferred = budget.run([(None, "raw")] * 20)
    assert results == [None] * 4
    assert deferred == 16
    assert budget.run([(None, "next source")]) == ([], 1)
    assert calls == [4]


def test_budget_is_cumulative_across_batches_and_sources():
    now = [0.0]
    allowances = []
    def runner(docs, timeout):
        allowances.append(timeout)
        now[0] += 7
        return [{"summary": "ok", "tickers": []}] * len(docs)
    budget = subject.EnrichmentBudget(seconds=10, runner=runner, clock=lambda: now[0])
    assert budget.run([(None, "a")])[1] == 0
    # Time spent ingesting raw documents does not consume optional work budget.
    now[0] += 500
    assert budget.run([(None, "b")])[1] == 0
    assert budget.run([(None, "c")]) == ([], 1)
    assert allowances == [10, 3]


def test_timeout_retains_completed_summaries_and_kills_worker_group(monkeypatch, tmp_path):
    """Ready handshake makes timeout independent of CI process startup speed."""
    real_popen = subprocess.Popen
    pid_file = tmp_path / "descendant.pid"
    code = '''
import json, subprocess, sys
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
open(sys.argv[1], "w").write(str(child.pid))
print("READY", flush=True)
print(json.dumps([0, {"summary": "finished", "tickers": []}]), flush=True)
child.wait()
'''
    processes = []
    def ready_popen(*args, **kwargs):
        proc = real_popen([sys.executable, "-c", code, str(pid_file)], **kwargs)
        assert proc.stdout.readline() == "READY\n"
        processes.append(proc)
        return proc
    monkeypatch.setattr(subject.subprocess, "Popen", ready_popen)
    result = subject.run_distill_batch([(None, "a"), (None, "b")], timeout=0.2)
    assert result == [{"summary": "finished", "tickers": []}, None]
    assert processes[0].poll() is not None
    descendant = int(pid_file.read_text())
    stat = Path(f"/proc/{descendant}/stat")
    assert not stat.exists() or stat.read_text().split()[2] == "Z"
