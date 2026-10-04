"""Optional enrichment must never consume the hourly ingestion window."""
from __future__ import annotations

from pathlib import Path
import os
import signal
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
        now[0] += min(7, timeout)
        return [{"summary": "ok", "tickers": []}] * len(docs)
    budget = subject.EnrichmentBudget(seconds=10, runner=runner, clock=lambda: now[0])
    assert budget.run([(None, "a")])[1] == 0
    # Time spent ingesting raw documents does not consume optional work budget.
    now[0] += 500
    assert budget.run([(None, "b")])[1] == 0
    assert budget.run([(None, "c")]) == ([], 1)
    assert allowances == [10, 3]


def test_timeout_retains_completed_summaries_and_kills_worker_group(monkeypatch, tmp_path):
    """Reap both owned group members without relying on Linux /proc or init."""
    real_popen = subprocess.Popen
    code = '''
import json, sys, time
print("READY", flush=True)
sys.stdin.read()
print(json.dumps([0, {"summary": "finished", "tickers": []}]), flush=True)
time.sleep(600)
'''
    processes = []
    def ready_popen(*args, **kwargs):
        assert kwargs["start_new_session"] is True
        # Keep the real separate group, but make pytest the parent of both
        # members so it can reap them before the product's final cleanup call.
        kwargs = {**kwargs, "start_new_session": False, "process_group": 0}
        proc = real_popen([sys.executable, "-c", code], **kwargs)
        processes.append(proc)
        assert proc.stdout.readline() == "READY\n"
        member = real_popen(
            [sys.executable, "-c", "import time; print('READY', flush=True); time.sleep(600)"],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, text=True,
            process_group=proc.pid,
        )
        processes.append(member)
        assert member.stdout.readline() == "READY\n"
        member.stdout.close()
        assert os.getpgid(member.pid) == proc.pid
        communicate = proc.communicate
        def communicate_and_reap_group(*args, **kwargs):
            output = communicate(*args, **kwargs)
            member.wait(timeout=5)
            return output
        monkeypatch.setattr(proc, "communicate", communicate_and_reap_group)
        return proc
    monkeypatch.setattr(subject.subprocess, "Popen", ready_popen)
    try:
        result = subject.run_distill_batch([(None, "a"), (None, "b")], timeout=0.2, clock=lambda: 0)
        assert result == [{"summary": "finished", "tickers": []}, None]
        assert processes[0].poll() is not None
        assert processes[1].poll() is not None
        assert processes[1].returncode == -signal.SIGKILL
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
            for pipe in (process.stdin, process.stdout):
                if pipe is not None:
                    pipe.close()


def test_main_shares_budget_across_source_retries(monkeypatch):
    import contextlib
    from types import SimpleNamespace
    from knowledge import ingest, sources
    from db import service_cycle
    observed = []
    def fake_ingest(db, module, **kwargs):
        observed.append(kwargs["enrichment"])
        if len(observed) == 1:
            raise RuntimeError("SQLITE_BUSY")
        return {"source": module.SOURCE}
    monkeypatch.setattr(ingest, "ingest_source", fake_ingest)
    monkeypatch.setattr(ingest, "_fresh_db", lambda: object())
    monkeypatch.setattr(ingest, "_load_dotenv_if_present", lambda: None)
    monkeypatch.setattr(ingest.time, "sleep", lambda _: None)
    monkeypatch.setattr(service_cycle, "service_cycle", lambda *a, **k: contextlib.nullcontext())
    monkeypatch.setattr(sources, "ALL_SOURCES", {name: SimpleNamespace(SOURCE=name) for name in ("a", "b")})
    assert ingest.main([]) == 0
    assert len(observed) == 3
    assert all(budget is observed[0] for budget in observed)


def test_cleanup_timeout_keeps_partial_result_and_closes_pipes(monkeypatch):
    import io
    class Process:
        pid = 123
        stdin = io.StringIO()
        stdout = io.StringIO()
        def communicate(self, *args, **kwargs):
            raise subprocess.TimeoutExpired("worker", 1, output=b'[0, {"summary":"kept", "tickers":[]}]\n')
        def wait(self, **kwargs):
            raise subprocess.TimeoutExpired("worker", 1)
    process = Process()
    kills = []
    monkeypatch.setattr(subject.subprocess, "Popen", lambda *a, **k: process)
    monkeypatch.setattr(subject.os, "killpg", lambda pid, signal: kills.append(pid))
    assert subject.run_distill_batch([(None, "raw")], 1) == [{"summary": "kept", "tickers": []}]
    assert kills == [123, 123]
    assert process.stdin.closed and process.stdout.closed
