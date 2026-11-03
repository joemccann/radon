"""Research asset + download archive to B2: confirm-before-prune contract.

Every test runs against a tmp research root and an in-memory fake S3 client.
No real B2, no host paths.
"""
import base64
import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone

import pytest

from research import asset_archive

NOW = datetime(2026, 10, 31, 4, 0, tzinfo=timezone.utc)
DAY = 86400
OLD = NOW.timestamp() - 45 * DAY
RECENT = NOW.timestamp() - 2 * DAY
CFG = {"bucket": "radon-archive", "prefix": "research/"}


class NotFound(Exception):
    def __init__(self):
        super().__init__("Not Found")
        self.response = {"Error": {"Code": "404"}}


class FakeS3:
    def __init__(self):
        self.objects = {}
        self.puts = []
        self.heads = []
        self.fail_put = set()
        self.corrupt_head = set()

    def put_object(self, Bucket, Key, Body, ContentLength, ContentMD5, Metadata):
        data = Body.read()
        if Key in self.fail_put:
            raise RuntimeError("AccessDenied")
        assert len(data) == ContentLength
        assert base64.b64encode(hashlib.md5(data).digest()).decode() == ContentMD5
        self.objects[(Bucket, Key)] = {"data": data, "meta": dict(Metadata)}
        self.puts.append(Key)
        return {}

    def head_object(self, Bucket, Key):
        self.heads.append(Key)
        obj = self.objects.get((Bucket, Key))
        if obj is None:
            raise NotFound()
        size = len(obj["data"]) + (1 if Key in self.corrupt_head else 0)
        return {"ContentLength": size, "Metadata": obj["meta"]}

    def seed(self, key, data):
        self.objects[("radon-archive", key)] = {"data": data, "meta": {"sha256": hashlib.sha256(data).hexdigest()}}


def _pdf(tag: str) -> bytes:
    return b"%PDF-1.7 " + tag.encode() * 8


def _asset(root, data, ext="pdf", when=OLD, atime=None):
    name = hashlib.sha256(data).hexdigest() + "." + ext
    path = root / "assets" / name
    path.write_bytes(data)
    os.utime(path, (atime if atime is not None else when, when))
    return name


def _download_name(path_lower, rev):
    return hashlib.sha256((path_lower + "\0" + rev).encode()).hexdigest() + ".pdf"


def _download(root, data, path_lower="/current/a.pdf", rev="r1", when=OLD):
    name = _download_name(path_lower, rev)
    path = root / "downloads" / name
    path.write_bytes(data)
    os.utime(path, (when, when))
    return name


def _state(root, work=(), ingestion=(), outbox=()):
    db = sqlite3.connect(root / "state.sqlite")
    db.execute("CREATE TABLE work(key TEXT PRIMARY KEY, metadata TEXT NOT NULL, status TEXT NOT NULL)")
    db.execute("CREATE TABLE ingestion(work_key TEXT PRIMARY KEY, status TEXT NOT NULL, pdf TEXT)")
    db.execute("CREATE TABLE outbox(id TEXT PRIMARY KEY, work_key TEXT NOT NULL, payload TEXT NOT NULL, "
               "status TEXT NOT NULL DEFAULT 'pending')")
    db.executemany("INSERT INTO outbox VALUES (?,?,?,?)", outbox)
    db.executemany("INSERT INTO work VALUES (?,?,?)",
                   [(key, json.dumps({"path_lower": p, "rev": r}), status) for key, p, r, status in work])
    db.executemany("INSERT INTO ingestion VALUES (?,?,?)", ingestion)
    db.commit()
    db.close()


@pytest.fixture(autouse=True)
def no_marketdesk_references(monkeypatch):
    """Turso is never reached from tests; each test opts into references."""
    monkeypatch.setattr(asset_archive, "_marketdesk_references", lambda: set())


@pytest.fixture
def root(tmp_path):
    root = tmp_path / "research"
    (root / "assets").mkdir(parents=True)
    (root / "downloads").mkdir()
    _state(root)
    return root


def _run(root, client, **kwargs):
    kwargs.setdefault("now", NOW)
    return asset_archive.run_archive(root, CFG, client=client, **kwargs)


class TestHotWindow:
    def test_hot_window_is_thirty_days(self):
        assert asset_archive.HOT_WINDOW_DAYS == 30

    def test_exactly_at_the_window_is_still_hot(self, root):
        name = _asset(root, _pdf("edge"), when=NOW.timestamp() - 30 * DAY)
        s3 = FakeS3()
        summary = _run(root, s3)
        assert f"research/assets/{name}" in s3.puts
        assert (root / "assets" / name).exists()
        assert summary["skipped"]["hot"] == 1

    def test_one_second_past_the_window_is_pruned(self, root):
        name = _asset(root, _pdf("past"), when=NOW.timestamp() - 30 * DAY - 1)
        s3 = FakeS3()
        summary = _run(root, s3)
        assert not (root / "assets" / name).exists()
        assert summary["pruned_assets"] == 1

    def test_recent_access_keeps_an_old_asset_hot(self, root):
        name = _asset(root, _pdf("read"), when=OLD, atime=RECENT)
        _run(root, FakeS3())
        assert (root / "assets" / name).exists()


class TestAssets:
    def test_old_asset_is_uploaded_verified_then_pruned(self, root):
        data = _pdf("one")
        name = _asset(root, data)
        s3 = FakeS3()
        summary = _run(root, s3)
        obj = s3.objects[("radon-archive", f"research/assets/{name}")]
        assert obj["data"] == data and obj["meta"]["sha256"] == name.split(".")[0]
        assert not (root / "assets" / name).exists()
        assert summary["uploaded_files"] == 1 and summary["uploaded_bytes"] == len(data)
        assert summary["pruned_assets"] == 1 and summary["pruned_bytes"] == len(data)

    def test_recent_asset_is_archived_but_kept(self, root):
        name = _asset(root, _pdf("new"), when=RECENT)
        s3 = FakeS3()
        _run(root, s3)
        assert f"research/assets/{name}" in s3.puts
        assert (root / "assets" / name).exists()

    def test_already_confirmed_asset_is_not_reuploaded(self, root):
        data = _pdf("again")
        name = _asset(root, data, when=RECENT)
        s3 = FakeS3()
        s3.seed(f"research/assets/{name}", data)
        summary = _run(root, s3)
        assert s3.puts == [] and summary["uploaded_files"] == 0 and summary["confirmed_assets"] == 1

    def test_failed_upload_never_prunes(self, root):
        name = _asset(root, _pdf("deny"))
        s3 = FakeS3()
        s3.fail_put.add(f"research/assets/{name}")
        summary = _run(root, s3)
        assert (root / "assets" / name).exists()
        assert summary["pruned_assets"] == 0 and summary["errors"]

    def test_failed_verification_never_prunes(self, root):
        name = _asset(root, _pdf("bad-head"))
        s3 = FakeS3()
        s3.corrupt_head.add(f"research/assets/{name}")
        summary = _run(root, s3)
        assert (root / "assets" / name).exists()
        assert summary["pruned_assets"] == 0 and summary["errors"]

    def test_object_with_wrong_digest_metadata_is_not_a_confirmation(self, root):
        data = _pdf("meta")
        name = _asset(root, data)
        s3 = FakeS3()
        s3.objects[("radon-archive", f"research/assets/{name}")] = {"data": data, "meta": {"sha256": "0" * 64}}
        s3.fail_put.add(f"research/assets/{name}")
        _run(root, s3)
        assert (root / "assets" / name).exists()

    def test_local_bytes_that_do_not_match_the_name_are_never_uploaded(self, root):
        name = hashlib.sha256(b"something else").hexdigest() + ".pdf"
        path = root / "assets" / name
        path.write_bytes(_pdf("tampered"))
        os.utime(path, (OLD, OLD))
        s3 = FakeS3()
        summary = _run(root, s3)
        assert s3.puts == [] and path.exists() and summary["errors"]

    def test_asset_changed_after_confirmation_is_kept(self, root, monkeypatch):
        name = _asset(root, _pdf("swap"))
        original = asset_archive._unlink_if_unchanged

        def touch_first(path, size, mtime_ns):
            os.utime(path, (OLD + 5, OLD + 5))
            return original(path, size, mtime_ns)

        monkeypatch.setattr(asset_archive, "_unlink_if_unchanged", touch_first)
        _run(root, FakeS3())
        assert (root / "assets" / name).exists()

    def test_temp_and_foreign_names_are_ignored(self, root):
        (root / "assets" / ".asset-abc").write_bytes(b"partial")
        (root / "assets" / "notes.txt").write_bytes(b"x")
        s3 = FakeS3()
        summary = _run(root, s3)
        assert s3.puts == [] and summary["skipped"]["unsafe"] == 2
        assert (root / "assets" / ".asset-abc").exists()

    def test_symlinked_asset_is_never_followed_or_pruned(self, root, tmp_path):
        target = tmp_path / "outside.pdf"
        target.write_bytes(_pdf("outside"))
        name = hashlib.sha256(target.read_bytes()).hexdigest() + ".pdf"
        (root / "assets" / name).symlink_to(target)
        s3 = FakeS3()
        _run(root, s3)
        assert s3.puts == [] and (root / "assets" / name).is_symlink() and target.exists()

    def test_dry_run_touches_neither_b2_nor_disk(self, root):
        name = _asset(root, _pdf("dry"))
        summary = _run(root, None, dry_run=True)
        assert (root / "assets" / name).exists() and summary["cold_assets"] == 1

    def test_no_prune_uploads_and_keeps(self, root):
        name = _asset(root, _pdf("keep"))
        s3 = FakeS3()
        _run(root, s3, prune=False)
        assert f"research/assets/{name}" in s3.puts and (root / "assets" / name).exists()

    def test_deadline_stops_new_work_cleanly(self, root):
        _asset(root, _pdf("late"))
        ticks = iter([0.0] + [10_000.0] * 50)
        summary = _run(root, FakeS3(), deadline_secs=60, clock=lambda: next(ticks))
        assert summary["skipped"]["deadline"] == 1 and summary["uploaded_files"] == 0

    def test_upload_budget_bounds_the_run(self, root):
        _asset(root, _pdf("a"))
        _asset(root, _pdf("b"))
        s3 = FakeS3()
        summary = _run(root, s3, max_bytes=len(_pdf("a")))
        assert len(s3.puts) == 1 and summary["skipped"]["budget"] == 1


class TestPendingReferences:
    """Unfinished work pins its files regardless of age; an unknown set pins everything."""

    def test_marketdesk_referenced_asset_past_the_window_is_kept(self, root, monkeypatch):
        data = _pdf("outbox")
        name = _asset(root, data)
        monkeypatch.setattr(asset_archive, "_marketdesk_references", lambda: {name.split(".")[0]})
        s3 = FakeS3()
        summary = _run(root, s3)
        assert f"research/assets/{name}" in s3.puts  # still archived
        assert (root / "assets" / name).exists()
        assert summary["skipped"]["referenced"] == 1 and summary["pruned_assets"] == 0

    def test_marketdesk_referenced_download_duplicate_is_kept(self, root, monkeypatch):
        data = _pdf("dl-ref")
        _asset(root, data)
        name = _download(root, data)
        monkeypatch.setattr(asset_archive, "_marketdesk_references",
                            lambda: {hashlib.sha256(data).hexdigest()})
        _run(root, FakeS3())
        assert (root / "downloads" / name).exists()

    def test_pending_research_outbox_payload_pins_its_assets(self, root):
        data = _pdf("payload")
        name = _asset(root, data)
        (root / "state.sqlite").unlink()
        _state(root, outbox=[("o1", "k1", json.dumps({"images": [f"/api/newsfeed/research/files/{name}"]}), "pending")])
        _run(root, FakeS3())
        assert (root / "assets" / name).exists()

    def test_published_research_outbox_payload_does_not_pin(self, root):
        data = _pdf("sent")
        name = _asset(root, data)
        (root / "state.sqlite").unlink()
        _state(root, outbox=[("o1", "k1", json.dumps({"url": f"/api/newsfeed/research/files/{name}"}), "published")])
        _run(root, FakeS3())
        assert not (root / "assets" / name).exists()

    def test_marketdesk_pdf_receipt_pins_its_asset(self, root):
        data = _pdf("receipt")
        name = _asset(root, data)
        receipts = root / "marketdesk" / "pdf-receipts"
        receipts.mkdir(parents=True)
        (receipts / "a1.json").write_text(json.dumps({"digest": name.split(".")[0],
                                                       "asset": f"/api/newsfeed/research/files/{name}"}))
        _run(root, FakeS3())
        assert (root / "assets" / name).exists()

    def test_lookup_failure_prunes_nothing_but_still_archives(self, root, monkeypatch):
        def down():
            raise OSError("turso unreachable")

        monkeypatch.setattr(asset_archive, "_marketdesk_references", down)
        data = _pdf("blind")
        name = _asset(root, data)
        download = _download(root, data)
        s3 = FakeS3()
        summary = _run(root, s3)
        assert f"research/assets/{name}" in s3.puts
        assert (root / "assets" / name).exists() and (root / "downloads" / download).exists()
        assert summary["pruned_bytes"] == 0 and summary["error_count"] == 1
        assert summary["errors"][0]["file"] == "pending-reference lookup"

    def test_corrupt_research_state_prunes_nothing(self, root):
        (root / "state.sqlite").write_bytes(b"not a database")
        name = _asset(root, _pdf("corrupt-state"))
        summary = _run(root, FakeS3())
        assert (root / "assets" / name).exists() and summary["error_count"] == 1

    def test_lookup_failure_heartbeats_error(self, root, monkeypatch):
        beats = []
        monkeypatch.setattr(asset_archive, "write_service_health",
                            lambda state, detail, started: beats.append((state, detail)))
        monkeypatch.setattr(asset_archive.archive, "s3_config_from_env", lambda env=None: dict(CFG))
        monkeypatch.setattr(asset_archive.archive, "_s3_client", lambda cfg, **kw: FakeS3())
        monkeypatch.setattr(asset_archive, "_marketdesk_references",
                            lambda: (_ for _ in ()).throw(RuntimeError("DbHttpError")))
        _asset(root, _pdf("hb"))
        assert asset_archive.main(["--root", str(root)]) == 1
        assert beats[-1][0] == "error"


class TestMarketDeskQuery:
    def test_query_covers_unfinished_articles_dropbox_outbox_and_unattached_pdfs(self, monkeypatch):
        seen = []

        def fake(sql, args=(), timeout=None):
            seen.append(sql)
            return [("a" * 64, "/api/newsfeed/research/files/" + "b" * 64 + ".pdf"), (None, None)]

        import api.db_http
        monkeypatch.setattr(api.db_http, "hrana_execute", fake)
        monkeypatch.undo()  # drop the autouse stub, keep nothing else
        monkeypatch.setattr(api.db_http, "hrana_execute", fake)
        refs = asset_archive._marketdesk_references()
        assert refs == {"a" * 64, "b" * 64}
        sql = " ".join(seen)
        assert "marketdesk_articles" in sql and "marketdesk_pdf_queue" in sql
        assert "dropbox_status IS NULL OR dropbox_status IN ('skipped_no_creds','failed')" in sql
        assert "attached_post_id IS NULL" in sql


class TestDownloads:
    def test_duplicate_of_a_local_asset_is_pruned(self, root):
        data = _pdf("dup")
        _asset(root, data, when=RECENT)
        name = _download(root, data)
        summary = _run(root, FakeS3())
        assert not (root / "downloads" / name).exists()
        assert summary["duplicate_downloads"] == 1 and summary["pruned_downloads"] == 1

    def test_duplicate_of_an_asset_confirmed_only_in_b2_is_pruned(self, root):
        data = _pdf("remote")
        digest = hashlib.sha256(data).hexdigest()
        s3 = FakeS3()
        s3.seed(f"research/assets/{digest}.pdf", data)
        name = _download(root, data, when=RECENT)
        _run(root, s3)
        assert not (root / "downloads" / name).exists()
        assert not any(key.startswith("research/downloads/") for key in s3.puts)

    def test_unconfirmed_remote_duplicate_is_kept(self, root):
        data = _pdf("nowhere")
        name = _download(root, data, when=RECENT)
        _run(root, FakeS3())
        assert (root / "downloads" / name).exists()

    @pytest.mark.parametrize("status", ["pending", "processing"])
    def test_in_flight_download_is_kept_even_when_duplicated_and_old(self, tmp_path, status):
        root = tmp_path / "r"
        (root / "assets").mkdir(parents=True)
        (root / "downloads").mkdir()
        data = _pdf("busy")
        _asset(root, data)
        name = _download(root, data, path_lower="/current/busy.pdf", rev="r9")
        _state(root, work=[("k1", "/current/busy.pdf", "r9", status)])
        summary = _run(root, FakeS3())
        assert (root / "downloads" / name).exists() and summary["skipped"]["in_flight"] == 1

    def test_ingestion_ready_path_of_pending_work_is_kept(self, tmp_path):
        root = tmp_path / "r"
        (root / "assets").mkdir(parents=True)
        (root / "downloads").mkdir()
        data = _pdf("ready")
        _asset(root, data)
        name = _download(root, data, path_lower="/x.pdf", rev="r1")
        # Metadata names a different revision; the ingestion row still points at the file.
        _state(root, work=[("k1", "/x.pdf", "r2", "pending")],
               ingestion=[("k1", "ready", str(root / "downloads" / name))])
        _run(root, FakeS3())
        assert (root / "downloads" / name).exists()

    def test_settled_work_does_not_protect_its_download(self, tmp_path):
        root = tmp_path / "r"
        (root / "assets").mkdir(parents=True)
        (root / "downloads").mkdir()
        data = _pdf("done")
        _asset(root, data)
        name = _download(root, data, path_lower="/d.pdf", rev="r1")
        _state(root, work=[("k1", "/d.pdf", "r1", "published")],
               ingestion=[("k1", "ready", str(root / "downloads" / name))])
        _run(root, FakeS3())
        assert not (root / "downloads" / name).exists()

    def test_requeued_between_scan_and_delete_is_kept(self, root, monkeypatch):
        data = _pdf("requeue")
        _asset(root, data)
        name = _download(root, data, path_lower="/q.pdf", rev="r1")
        calls = {"n": 0}
        original = asset_archive._in_flight_downloads

        def flips(r):
            calls["n"] += 1
            return original(r) if calls["n"] == 1 else {name}

        monkeypatch.setattr(asset_archive, "_in_flight_downloads", flips)
        _run(root, FakeS3())
        assert (root / "downloads" / name).exists()

    def test_unique_recent_download_is_kept_and_not_uploaded(self, root):
        name = _download(root, _pdf("fresh"), when=RECENT)
        s3 = FakeS3()
        _run(root, s3)
        assert (root / "downloads" / name).exists() and s3.puts == []

    def test_unique_old_download_is_archived_then_pruned(self, root):
        data = _pdf("unique")
        name = _download(root, data)
        s3 = FakeS3()
        summary = _run(root, s3)
        obj = s3.objects[("radon-archive", f"research/downloads/{name}")]
        assert obj["data"] == data and obj["meta"]["sha256"] == hashlib.sha256(data).hexdigest()
        assert not (root / "downloads" / name).exists() and summary["archived_downloads"] == 1

    def test_unique_old_download_with_failed_upload_is_kept(self, root):
        name = _download(root, _pdf("stuck"))
        s3 = FakeS3()
        s3.fail_put.add(f"research/downloads/{name}")
        summary = _run(root, s3)
        assert (root / "downloads" / name).exists() and summary["errors"]

    def test_missing_state_leaves_downloads_untouched(self, root):
        (root / "state.sqlite").unlink()
        data = _pdf("nostate")
        _asset(root, data)
        name = _download(root, data)
        summary = _run(root, FakeS3())
        assert (root / "downloads" / name).exists() and summary["skipped"]["no_state"] == 1

    def test_young_file_is_left_for_the_writer(self, root):
        data = _pdf("young")
        _asset(root, data)
        name = _download(root, data, when=NOW.timestamp() - 60)
        _run(root, FakeS3())
        assert (root / "downloads" / name).exists()


class TestConfigAndMain:
    def _patch(self, monkeypatch, s3):
        beats = []
        monkeypatch.setattr(asset_archive, "write_service_health",
                            lambda state, detail, started: beats.append((state, detail)))
        monkeypatch.setattr(asset_archive.archive, "s3_config_from_env", lambda env=None: dict(CFG))
        monkeypatch.setattr(asset_archive.archive, "_s3_client", lambda cfg, **kw: s3)
        return beats

    def test_service_name(self):
        assert asset_archive.SERVICE_NAME == "research-asset-archive"

    def test_missing_credentials_fail_closed_with_an_error_heartbeat(self, root, monkeypatch):
        name = _asset(root, _pdf("creds"))
        beats = []
        monkeypatch.setattr(asset_archive, "write_service_health",
                            lambda state, detail, started: beats.append((state, detail)))
        monkeypatch.setattr(asset_archive.archive, "s3_config_from_env", lambda env=None: None)
        assert asset_archive.main(["--root", str(root)]) == 2
        assert beats[-1][0] == "error" and (root / "assets" / name).exists()

    def test_ok_heartbeat_carries_bytes_uploaded_and_pruned(self, root, monkeypatch):
        data = _pdf("beat")
        _asset(root, data)
        _download(root, data)
        beats = self._patch(monkeypatch, FakeS3())
        assert asset_archive.main(["--root", str(root)]) == 0
        state, detail = beats[-1]
        assert state == "ok"
        assert detail["uploaded_bytes"] == len(data)
        assert detail["pruned_bytes"] == 2 * len(data)

    def test_error_heartbeat_when_any_file_fails(self, root, monkeypatch):
        name = _asset(root, _pdf("fail"))
        s3 = FakeS3()
        s3.fail_put.add(f"research/assets/{name}")
        beats = self._patch(monkeypatch, s3)
        assert asset_archive.main(["--root", str(root)]) == 1
        assert beats[-1][0] == "error" and beats[-1][1]["errors"]

    def test_crash_is_an_error_heartbeat(self, root, monkeypatch):
        beats = self._patch(monkeypatch, FakeS3())
        monkeypatch.setattr(asset_archive, "run_archive", lambda *a, **k: (_ for _ in ()).throw(OSError("disk")))
        assert asset_archive.main(["--root", str(root)]) == 1
        assert beats[-1][0] == "error"

    def test_default_deadline_leaves_headroom_under_the_unit_timeout(self):
        from pathlib import Path
        unit = (Path(__file__).resolve().parents[2] / "cloud/services/radon-research-asset-archive.service").read_text()
        timeout = int(next(l.split("=", 1)[1] for l in unit.splitlines() if l.startswith("TimeoutStartSec=")))
        assert asset_archive.DEFAULT_DEADLINE_SECS <= timeout - 1800
