"""Monthly research-evidence archive to B2: verify-before-delete contract.

Every test runs against a tmp research root and an in-memory fake S3 client.
No real B2, no host paths.
"""
import base64
import hashlib
import os
import sqlite3
from datetime import datetime, timezone

import pytest

from research import archive

NOW = datetime(2026, 10, 1, 5, 30, tzinfo=timezone.utc)
OLD = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc).timestamp()
RECENT = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc).timestamp()
CFG = {"bucket": "radon-archive", "prefix": "research/"}


class NotFound(Exception):
    def __init__(self):
        super().__init__("Not Found")
        self.response = {"Error": {"Code": "404"}}


class FakeS3:
    def __init__(self):
        self.objects = {}
        self.puts = []
        self.heads = 0
        self.fail_put = set()
        self.corrupt_head = set()

    def put_object(self, Bucket, Key, Body, ContentLength, ContentMD5, Metadata):
        data = Body.read()
        if Key in self.fail_put:
            raise RuntimeError("AccessDenied")
        assert len(data) == ContentLength
        # B2 rejects a body whose MD5 differs from Content-MD5 (BadDigest).
        assert base64.b64encode(hashlib.md5(data).digest()).decode() == ContentMD5
        self.objects[(Bucket, Key)] = {"data": data, "meta": dict(Metadata)}
        self.puts.append(Key)
        return {}

    def head_object(self, Bucket, Key):
        self.heads += 1
        obj = self.objects.get((Bucket, Key))
        if obj is None:
            raise NotFound()
        size = len(obj["data"]) + (1 if Key in self.corrupt_head else 0)
        return {"ContentLength": size, "Metadata": obj["meta"]}


def _state(root, rows):
    db = sqlite3.connect(root / "state.sqlite")
    db.execute("CREATE TABLE work(key TEXT PRIMARY KEY, status TEXT NOT NULL)")
    db.executemany("INSERT INTO work VALUES (?,?)", rows)
    db.commit()
    db.close()


def _evidence(root, key, mtime, files=None):
    directory = root / "evidence" / key
    directory.mkdir(parents=True)
    for name, body in (files or {"evidence.json": b'{"pages":[]}', "page-0001.md": b"# p1"}).items():
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        os.utime(path, (mtime, mtime))
    return directory


@pytest.fixture
def root(tmp_path):
    root = tmp_path / "research"
    (root / "evidence").mkdir(parents=True)
    return root


def _run(root, client, **kwargs):
    kwargs.setdefault("now", NOW)
    return archive.run_archive(root, CFG, client=client, **kwargs)


class TestCutoff:
    # Rolling window: the cutoff no longer snaps to the start of the month.
    # A month-start cutoff left October's 10.6G of evidence on the 75G app
    # host disk until Nov 1 and the 2026-10-21 deploy hit ENOSPC.
    def test_first_of_month_keeps_the_last_week(self):
        assert archive.compute_cutoff(NOW, 7) == datetime(2026, 9, 24, 5, 30, tzinfo=timezone.utc)

    def test_mid_month_cuts_a_rolling_week_back(self):
        mid = datetime(2026, 10, 15, tzinfo=timezone.utc)
        assert archive.compute_cutoff(mid, 7) == datetime(2026, 10, 8, tzinfo=timezone.utc)

    def test_min_age_is_honoured_not_the_calendar(self):
        mid = datetime(2026, 10, 15, tzinfo=timezone.utc)
        assert archive.compute_cutoff(mid, 14) == datetime(2026, 10, 1, tzinfo=timezone.utc)
        assert archive.compute_cutoff(mid, 1) == datetime(2026, 10, 14, tzinfo=timezone.utc)


class TestArchiveAndPrune:
    def test_old_terminal_dir_is_uploaded_verified_then_pruned(self, root):
        _state(root, [("k1", "published")])
        directory = _evidence(root, "k1", OLD, {"evidence.json": b"{}", "sub/page-0001.md": b"text"})
        s3 = FakeS3()
        summary = _run(root, s3)
        assert summary["archived_dirs"] == 1 and summary["pruned_dirs"] == 1 and summary["errors"] == []
        assert sorted(s3.puts) == ["research/evidence/2026-08/k1/evidence.json",
                                   "research/evidence/2026-08/k1/sub/page-0001.md"]
        stored = s3.objects[("radon-archive", "research/evidence/2026-08/k1/sub/page-0001.md")]
        assert stored["data"] == b"text"
        assert stored["meta"]["sha256"] == hashlib.sha256(b"text").hexdigest()
        assert not directory.exists()
        assert (root / "evidence").is_dir()

    def test_recent_dir_is_untouched(self, root):
        _state(root, [("k1", "complete")])
        directory = _evidence(root, "k1", RECENT)
        s3 = FakeS3()
        summary = _run(root, s3)
        assert s3.puts == [] and directory.exists() and summary["skipped"]["recent"] == 1

    @pytest.mark.parametrize("status", ["pending", "processing"])
    def test_in_flight_dir_is_untouched_even_when_old(self, root, status):
        _state(root, [("k1", status)])
        directory = _evidence(root, "k1", OLD)
        s3 = FakeS3()
        summary = _run(root, s3)
        assert s3.puts == [] and directory.exists() and summary["skipped"]["in_flight"] == 1

    def test_orphan_dir_without_a_work_row_is_archived(self, root):
        _state(root, [])
        _evidence(root, "orphan", OLD)
        summary = _run(root, FakeS3())
        assert summary["pruned_dirs"] == 1

    def test_failed_verification_deletes_nothing(self, root):
        _state(root, [("k1", "complete")])
        directory = _evidence(root, "k1", OLD)
        s3 = FakeS3()
        s3.corrupt_head.add("research/evidence/2026-08/k1/page-0001.md")
        summary = _run(root, s3)
        assert summary["pruned_dirs"] == 0 and len(summary["errors"]) == 1
        assert (directory / "page-0001.md").exists() and (directory / "evidence.json").exists()

    def test_failed_upload_deletes_nothing_and_other_dirs_continue(self, root):
        _state(root, [("bad", "complete"), ("good", "complete")])
        bad = _evidence(root, "bad", OLD)
        good = _evidence(root, "good", OLD)
        s3 = FakeS3()
        s3.fail_put.add("research/evidence/2026-08/bad/evidence.json")
        summary = _run(root, s3)
        assert bad.exists() and (bad / "page-0001.md").exists()
        assert not good.exists()
        assert summary["pruned_dirs"] == 1 and len(summary["errors"]) == 1

    def test_rerun_after_no_delete_is_idempotent(self, root):
        _state(root, [("k1", "complete")])
        directory = _evidence(root, "k1", OLD)
        s3 = FakeS3()
        first = _run(root, s3, prune=False)
        assert first["archived_dirs"] == 1 and first["pruned_dirs"] == 0 and directory.exists()
        uploads = len(s3.puts)
        second = _run(root, s3)
        assert len(s3.puts) == uploads, "verified objects must not be re-uploaded"
        assert second["pruned_dirs"] == 1 and not directory.exists()

    def test_dry_run_touches_neither_b2_nor_disk(self, root):
        _state(root, [("k1", "complete")])
        directory = _evidence(root, "k1", OLD)
        summary = archive.run_archive(root, CFG, client=None, now=NOW, dry_run=True)
        assert summary["candidate_dirs"] == 1 and summary["candidate_bytes"] > 0
        assert summary["pruned_dirs"] == 0 and directory.exists()

    def test_symlink_inside_dir_makes_it_ineligible(self, root, tmp_path):
        _state(root, [("k1", "complete")])
        directory = _evidence(root, "k1", OLD)
        outside = tmp_path / "outside"
        outside.write_text("secret")
        (directory / "link").symlink_to(outside)
        s3 = FakeS3()
        summary = _run(root, s3)
        assert s3.puts == [] and directory.exists() and outside.exists()
        assert summary["skipped"]["unsafe"] == 1

    def test_file_changed_after_verification_is_kept(self, root, monkeypatch):
        _state(root, [("k1", "complete")])
        directory = _evidence(root, "k1", OLD)
        real = archive._verify_object

        def touch_after_verify(client, bucket, key, local):
            ok = real(client, bucket, key, local)
            if ok and key.endswith("page-0001.md"):
                (directory / "page-0001.md").write_bytes(b"rewritten by the worker")
            return ok

        monkeypatch.setattr(archive, "_verify_object", touch_after_verify)
        summary = _run(root, FakeS3())
        assert (directory / "page-0001.md").read_bytes() == b"rewritten by the worker"
        assert not (directory / "evidence.json").exists()
        assert summary["pruned_dirs"] == 0

    def test_requeued_between_upload_and_delete_is_kept(self, root, monkeypatch):
        _state(root, [("k1", "complete")])
        directory = _evidence(root, "k1", OLD)
        real = archive._archive_dir

        def requeue(*args, **kwargs):
            result = real(*args, **kwargs)
            db = sqlite3.connect(root / "state.sqlite")
            db.execute("UPDATE work SET status='pending' WHERE key='k1'")
            db.commit()
            db.close()
            return result

        monkeypatch.setattr(archive, "_archive_dir", requeue)
        summary = _run(root, FakeS3())
        assert directory.exists() and (directory / "page-0001.md").exists()
        assert summary["pruned_dirs"] == 0

    def test_missing_state_fails_closed(self, root):
        _evidence(root, "k1", OLD)
        with pytest.raises(FileNotFoundError):
            _run(root, FakeS3())

    def test_host_without_research_is_a_clean_noop(self, tmp_path):
        summary = archive.run_archive(tmp_path / "absent", CFG, client=FakeS3(), now=NOW)
        assert summary["candidate_dirs"] == 0 and summary["errors"] == []

    def test_byte_budget_bounds_the_run(self, root):
        _state(root, [("a", "complete"), ("b", "complete")])
        _evidence(root, "a", OLD, {"x.md": b"1" * 100})
        second = _evidence(root, "b", OLD, {"x.md": b"2" * 100})
        summary = _run(root, FakeS3(), max_bytes=150)
        assert summary["pruned_dirs"] == 1 and second.exists()
        assert summary["skipped"]["budget"] == 1

    def test_dir_budget_bounds_the_run(self, root):
        _state(root, [("a", "complete"), ("b", "complete")])
        _evidence(root, "a", OLD)
        _evidence(root, "b", OLD)
        summary = _run(root, FakeS3(), max_dirs=1)
        assert summary["pruned_dirs"] == 1 and summary["skipped"]["budget"] == 1

    def test_never_touches_assets_or_downloads(self, root):
        _state(root, [("k1", "complete")])
        _evidence(root, "k1", OLD)
        for sibling in ("assets", "downloads"):
            (root / sibling).mkdir()
            path = root / sibling / "f.pdf"
            path.write_bytes(b"%PDF-")
            os.utime(path, (OLD, OLD))
        s3 = FakeS3()
        _run(root, s3)
        assert all(key.startswith("research/evidence/") for key in s3.puts)
        assert (root / "assets" / "f.pdf").exists() and (root / "downloads" / "f.pdf").exists()


class TestConfigAndMain:
    def test_config_reuses_the_archive_bucket_with_its_own_prefix(self):
        env = {"RADON_ARCHIVE_S3_ENDPOINT": "s3.us-west-004.backblazeb2.com",
               "RADON_ARCHIVE_S3_BUCKET": "radon-archive", "RADON_ARCHIVE_S3_ACCESS_KEY_ID": "id",
               "RADON_ARCHIVE_S3_SECRET_ACCESS_KEY": "secret", "RADON_ARCHIVE_S3_PREFIX": "portfolio_snapshots/"}
        cfg = archive.s3_config_from_env(env)
        assert cfg["endpoint_url"] == "https://s3.us-west-004.backblazeb2.com"
        assert cfg["prefix"] == "research/" and cfg["region"] == "auto"
        assert archive.s3_config_from_env({**env, "RADON_RESEARCH_ARCHIVE_PREFIX": "r2"})["prefix"] == "r2/"
        assert archive.s3_config_from_env({}) is None

    def test_missing_credentials_fail_closed_with_an_error_heartbeat(self, root, monkeypatch):
        beats = []
        monkeypatch.setattr(archive, "write_service_health", lambda state, detail, started: beats.append((state, detail)))
        for name in list(os.environ):
            if name.startswith("RADON_ARCHIVE_S3_"):
                monkeypatch.delenv(name)
        assert archive.main(["--root", str(root)]) == 2
        assert beats and beats[-1][0] == "error"

    def test_main_heartbeats_ok_on_a_clean_run(self, root, monkeypatch):
        _state(root, [("k1", "complete")])
        _evidence(root, "k1", OLD)
        beats = []
        monkeypatch.setattr(archive, "write_service_health", lambda state, detail, started: beats.append((state, detail)))
        monkeypatch.setattr(archive, "s3_config_from_env", lambda env=None: dict(CFG))
        s3 = FakeS3()
        monkeypatch.setattr(archive, "_s3_client", lambda cfg: s3)
        assert archive.main(["--root", str(root)]) == 0
        assert beats[-1][0] == "ok" and beats[-1][1]["pruned_dirs"] == 1

    def test_main_reports_error_when_any_dir_fails(self, root, monkeypatch):
        _state(root, [("k1", "complete")])
        _evidence(root, "k1", OLD)
        beats = []
        monkeypatch.setattr(archive, "write_service_health", lambda state, detail, started: beats.append((state, detail)))
        monkeypatch.setattr(archive, "s3_config_from_env", lambda env=None: dict(CFG))
        s3 = FakeS3()
        s3.fail_put.add("research/evidence/2026-08/k1/evidence.json")
        monkeypatch.setattr(archive, "_s3_client", lambda cfg: s3)
        monkeypatch.setattr(archive, "_utcnow", lambda: NOW)
        assert archive.main(["--root", str(root)]) == 1
        assert beats[-1][0] == "error"


class SlowS3(FakeS3):
    """Each call holds a slot briefly so overlapping calls are observable."""

    def __init__(self):
        super().__init__()
        import threading
        self._lock = threading.Lock()
        self.inflight = 0
        self.peak = 0

    def _enter(self):
        import time as _time
        with self._lock:
            self.inflight += 1
            self.peak = max(self.peak, self.inflight)
        _time.sleep(0.02)
        with self._lock:
            self.inflight -= 1

    def put_object(self, **kw):
        self._enter()
        return super().put_object(**kw)

    def head_object(self, **kw):
        self._enter()
        return super().head_object(**kw)


class TestThroughput:
    def test_files_in_one_dir_upload_concurrently(self, root):
        files = {f"page-{i:04d}.md": f"# {i}".encode() for i in range(16)}
        _state(root, [])
        _evidence(root, "w-many", OLD, files)
        client = SlowS3()
        summary = _run(root, client, workers=8)
        assert summary["pruned_dirs"] == 1 and summary["uploaded_files"] == 16
        assert client.peak > 1, "per-file B2 round trips must overlap"

    def test_one_failed_file_in_a_parallel_dir_still_deletes_nothing(self, root):
        files = {f"page-{i:04d}.md": f"# {i}".encode() for i in range(8)}
        _state(root, [])
        directory = _evidence(root, "w-bad", OLD, files)
        client = SlowS3()
        client.fail_put.add("research/evidence/2026-08/w-bad/page-0005.md")
        summary = _run(root, client, workers=8)
        assert summary["pruned_dirs"] == 0 and len(summary["errors"]) == 1
        assert sorted(p.name for p in directory.iterdir()) == sorted(files)

    def test_deadline_stops_new_dirs_cleanly_before_the_unit_timeout(self, root):
        _state(root, [])
        for key in ("w-a", "w-b", "w-c"):
            _evidence(root, key, OLD)
        ticks = iter([0.0, 0.0, 100.0, 100.0, 100.0, 100.0])
        summary = _run(root, FakeS3(), deadline_secs=50, clock=lambda: next(ticks, 100.0))
        assert summary["pruned_dirs"] == 1
        assert summary["skipped"]["deadline"] == 2
        assert summary["errors"] == []
        assert (root / "evidence" / "w-b").exists() and (root / "evidence" / "w-c").exists()

    def test_default_deadline_leaves_headroom_under_the_unit_timeout(self):
        unit = (archive.Path(archive.__file__).resolve().parents[2]
                / "cloud" / "services" / "radon-research-archive.service").read_text()
        timeout = int(next(l.split("=", 1)[1] for l in unit.splitlines() if l.startswith("TimeoutStartSec=")))
        assert archive.DEFAULT_DEADLINE_SECS <= timeout - 1800
