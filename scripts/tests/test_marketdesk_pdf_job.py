"""A separately throttled archive cannot hold article publication hostage."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from textwrap import dedent
from pathlib import Path
from types import SimpleNamespace

import pytest

from research import marketdesk_pdf_job as job
from research.marketdesk_auth import MarketDeskError

NOW = datetime(2026, 10, 27, 17, tzinfo=timezone.utc)


@contextmanager
def cycle(*args, **kwargs):
    yield SimpleNamespace(finished_at=None)


class Queue:
    def __init__(self):
        self.work = SimpleNamespace(article_id="ABCdef12345", token="lease")
        self.downloads = []
        self.retries = []
        self.claims = []
    def seed_missing(self, **kwargs):
        return 35
    def attach_downloads(self, **kwargs):
        return 0
    def claim_next(self, **kwargs):
        self.claims.append(kwargs)
        work, self.work = self.work, None
        return work
    def record_download(self, work, url, digest, **kwargs):
        self.downloads.append((work.article_id, url, digest))
        return True
    def retry(self, work, code, **kwargs):
        self.retries.append((code, kwargs))
    def blocked(self):
        return self.retries[-1][0] if self.retries else None


def deps(tmp_path, *, download=None):
    queue = Queue()
    asset = "/api/newsfeed/research/files/" + hashlib.sha256(b"%PDF-fake").hexdigest() + ".pdf"
    return SimpleNamespace(store=queue, open_session=lambda env: object(),
                           download=download or (lambda session, key: b"%PDF-fake"),
                           store_asset=lambda path: asset, read_asset=lambda name: b"%PDF-fake",
                           service_cycle=cycle, root=lambda: tmp_path, now=lambda: NOW)


def test_one_attempt_per_cycle_and_persisted_admission(tmp_path):
    dependency = deps(tmp_path)
    counts = job.run_cycle(env={"MARKETDESK_PDF_MIN_INTERVAL_SECONDS": "301"}, deps=dependency)
    assert counts == {"seeded": 35, "attached": 0, "downloaded": 1, "throttled": 0}
    assert len(dependency.store.claims) == len(dependency.store.downloads) == 1
    assert dependency.store.claims[0]["interval_seconds"] == 301
    assert (tmp_path / "jobs/marketdesk-pdfs/state.json").is_file()
    assert (tmp_path / "jobs/marketdesk-pdfs/findings.jsonl").is_file()
    assert (tmp_path / "jobs/marketdesk-pdfs/SUMMARY.md").is_file()


def test_no_work_does_not_authenticate_or_download(tmp_path):
    dependency = deps(tmp_path)
    dependency.store.work = None
    dependency.open_session = lambda env: pytest.fail("throttle must happen before sign in")
    assert job.run_cycle(env={}, deps=dependency)["throttled"] == 1


@pytest.mark.parametrize("code", ["preview_quota", "download_quota"])
def test_persisted_allowance_backoff_keeps_error_and_exits_zero(tmp_path, monkeypatch, capsys, code):
    """A durable provider allowance is already the error row. Exit 1 pages P1."""
    recorded = []

    @contextmanager
    def recording(*args, **kwargs):
        try:
            yield SimpleNamespace(finished_at=None)
        except BaseException as exc:
            recorded.append(str(exc))
            raise

    dependency = deps(tmp_path)
    dependency.service_cycle = recording
    dependency.store.work = None
    dependency.store.blocked = lambda: code
    dependency.open_session = lambda env: pytest.fail("persisted allowance must not sign in")
    monkeypatch.setattr(job, "default_deps", lambda: dependency)
    assert job.main([]) == 0
    assert recorded == ["PDF archive deferred: " + code]
    assert "PDF archive deferred: " + code in capsys.readouterr().err


def test_preview_quota_download_exits_zero_after_durable_retry(tmp_path, monkeypatch):
    def fail(session, key):
        raise MarketDeskError("preview_quota")

    dependency = deps(tmp_path, download=fail)
    monkeypatch.setattr(job, "default_deps", lambda: dependency)
    assert job.main([]) == 0
    assert dependency.store.retries[0][0] == "preview_quota"
    assert dependency.store.retries[0][1]["backoff_seconds"] >= 3600
    assert not dependency.store.downloads


@pytest.mark.parametrize("code", ["not_pdf", "http_transport", "unauthenticated"])
def test_non_allowance_deferral_still_fails_the_oneshot(tmp_path, monkeypatch, code):
    dependency = deps(tmp_path)
    dependency.store.work = None
    dependency.store.blocked = lambda: code
    monkeypatch.setattr(job, "default_deps", lambda: dependency)
    assert job.main([]) == 1


def test_unexpected_cycle_error_still_fails_the_oneshot(monkeypatch):
    def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(job, "run_cycle", boom)
    assert job.main([]) == 1


@pytest.mark.parametrize("code", ["download_quota", "preview_quota", "http_429", "http_transport"])
def test_provider_failure_backoff_is_visible_and_durable(tmp_path, code):
    def fail(session, key):
        raise MarketDeskError(code)
    dependency = deps(tmp_path, download=fail)
    with pytest.raises(job.DownloadDeferred, match=code):
        job.run_cycle(env={}, deps=dependency)
    assert dependency.store.retries[0][0] == code
    assert dependency.store.retries[0][1]["backoff_seconds"] >= 900
    assert not dependency.store.downloads


def test_restart_heals_receipt_without_consuming_second_download(tmp_path):
    dependency = deps(tmp_path)
    original = dependency.store.record_download
    dependency.store.record_download = lambda *args, **kwargs: (_ for _ in ()).throw(ConnectionError("db down"))
    with pytest.raises(ConnectionError):
        job.run_cycle(env={}, deps=dependency)
    dependency.store.record_download = original
    dependency.store.work = SimpleNamespace(article_id="ABCdef12345", token="newlease")
    dependency.download = lambda *args: pytest.fail("PDF already durably saved")
    dependency.open_session = lambda env: pytest.fail("receipt recovery is local")
    assert job.run_cycle(env={}, deps=dependency)["downloaded"] == 1


def test_pdf_content_is_verified_before_asset_commit(tmp_path):
    dependency = deps(tmp_path, download=lambda *args: b"<html>login</html>")
    dependency.store_asset = lambda path: pytest.fail("HTML must not be stored as PDF")
    with pytest.raises(job.DownloadDeferred, match="not_pdf"):
        job.run_cycle(env={}, deps=dependency)


def test_default_download_uses_existing_authorized_pdf_transport():
    class Session:
        def blob(self, path, *, limit):
            assert path == "/files/ABCdef12345/blob"
            assert limit == job.MAX_PDF_BYTES
            return b"%PDF-real", "application/pdf"
    assert job.download_pdf(Session(), "ABCdef12345") == b"%PDF-real"


def test_default_download_preserves_provider_allowance_denial():
    class Session:
        def blob(self, path, *, limit):
            assert path == "/files/ABCdef12345/blob"
            raise MarketDeskError("preview_quota")
    with pytest.raises(MarketDeskError, match="preview_quota"):
        job.download_pdf(Session(), "ABCdef12345")


@pytest.mark.parametrize("stage", ["receipt", "wal", "database"])
def test_sigkill_at_each_ack_boundary_resumes_without_second_download(tmp_path, stage):
    """Three actual SIGKILLs cover the receipt/WAL/DB acknowledgement windows."""
    worker = tmp_path / "worker.py"
    worker.write_text(dedent('''
        from contextlib import contextmanager
        from datetime import datetime,timezone
        import hashlib,os,sys,time
        from pathlib import Path
        from types import SimpleNamespace
        from research import marketdesk_pdf_job as job
        root=Path(sys.argv[1]); stage=sys.argv[2]
        def boundary(name):
            if stage==name:
                (root/'ready').write_text(name)
                while True: time.sleep(.05)
        @contextmanager
        def cycle(*a,**k): yield SimpleNamespace(finished_at=None)
        class Store:
            def seed_missing(self,**k): return 0
            def attach_downloads(self,**k): return 0
            def claim_next(self,**k):
                return None if (root/'ack').exists() else SimpleNamespace(article_id='ABCdef12345',token='new')
            def record_download(self,*a,**k):
                (root/'ack').write_text('1'); boundary('database'); return True
            def retry(self,*a,**k): raise AssertionError('unexpected retry')
            def blocked(self): return None
        def download(*a):
            with (root/'requests').open('a') as out: out.write('download\\n'); out.flush(); os.fsync(out.fileno())
            return b'%PDF-real'
        def asset(path):
            data=path.read_bytes(); digest=hashlib.sha256(data).hexdigest()
            (root/(digest+'.pdf')).write_bytes(data)
            return job.ASSET_PREFIX+digest+'.pdf'
        save=job.atomic_save
        def save_receipt(*a,**k):
            result=save(*a,**k); boundary('receipt'); return result
        job.atomic_save=save_receipt
        record=job.CheckpointedJob.record_finding
        def record_wal(self,*a,**k):
            result=record(self,*a,**k); boundary('wal'); return result
        job.CheckpointedJob.record_finding=record_wal
        deps=SimpleNamespace(store=Store(),open_session=lambda env: object(),download=download,store_asset=asset,
            read_asset=lambda name:(root/name).read_bytes(),service_cycle=cycle,root=lambda:root,
            now=lambda:datetime.now(timezone.utc))
        job.run_cycle(env={},deps=deps)
    '''))
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
    child = subprocess.Popen([sys.executable, str(worker), str(tmp_path), stage], env=env,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 10
        while not (tmp_path / "ready").exists() and time.monotonic() < deadline and child.poll() is None:
            time.sleep(.01)
        assert (tmp_path / "ready").exists(), child.communicate(timeout=1)
        os.kill(child.pid, signal.SIGKILL)
        child.communicate(timeout=5)
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate(timeout=5)
    resumed = subprocess.run([sys.executable, str(worker), str(tmp_path), "resume"], env=env,
                             capture_output=True, text=True, timeout=10)
    assert resumed.returncode == 0, resumed.stderr
    assert (tmp_path / "requests").read_text().splitlines() == ["download"]
    assert (tmp_path / "ack").read_text() == "1"
    wal = (tmp_path / "jobs/marketdesk-pdfs/findings.jsonl").read_text().splitlines()
    assert len(wal) == 1
    assert json.loads(wal[0])["asset"].endswith(".pdf")
