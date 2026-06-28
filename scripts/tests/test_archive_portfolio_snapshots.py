"""Unit + integration tests for the portfolio_snapshots cold-archive pipeline."""
from __future__ import annotations

import gzip
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import archive_portfolio_snapshots as arch  # noqa: E402


# --------------------------------------------------------------------------
# Pure helpers
# --------------------------------------------------------------------------
def test_compute_cutoff_iso_format_and_value():
    now = datetime(2026, 6, 28, 12, 0, 0, tzinfo=timezone.utc)
    cutoff = arch.compute_cutoff_iso(now, 30)
    assert cutoff == "2026-05-29T12:00:00.000000Z"
    # Lexicographically comparable with taken_at-style strings.
    assert "2026-05-03T06:10:41.235168Z" < cutoff
    assert "2026-06-10T00:00:00.000000Z" > cutoff


def test_month_key():
    assert arch.month_key("2026-05-03T06:10:41.235168Z") == "2026-05"


def test_group_by_month():
    rows = [
        ("2026-05-03T00:00:00Z", "{}"),
        ("2026-05-09T00:00:00Z", "{}"),
        ("2026-06-01T00:00:00Z", "{}"),
    ]
    grouped = arch.group_by_month(rows)
    assert set(grouped) == {"2026-05", "2026-06"}
    assert len(grouped["2026-05"]) == 2
    assert len(grouped["2026-06"]) == 1


def test_rows_to_jsonl_bytes_parses_payload():
    rows = [("2026-05-03T00:00:00Z", '{"nav": 100}')]
    out = arch.rows_to_jsonl_bytes(rows).decode()
    obj = json.loads(out.strip())
    assert obj["taken_at"] == "2026-05-03T00:00:00Z"
    assert obj["payload"] == {"nav": 100}


def test_rows_to_jsonl_bytes_raw_fallback_for_bad_json():
    rows = [("2026-05-03T00:00:00Z", "not json")]
    obj = json.loads(arch.rows_to_jsonl_bytes(rows).decode().strip())
    assert obj["payload"] == "not json"


def test_partition_roundtrip(tmp_path):
    path = tmp_path / "2026-05.jsonl.gz"
    data = arch.rows_to_jsonl_bytes([("2026-05-03T00:00:00Z", '{"a": 1}')])
    arch.write_partition_atomic(path, data)
    assert path.exists()
    rows = arch.read_partition_rows(path)
    assert set(rows) == {"2026-05-03T00:00:00Z"}


def test_read_partition_missing_file_is_empty(tmp_path):
    assert arch.read_partition_rows(tmp_path / "nope.jsonl.gz") == {}


def test_merge_partition_dedups_and_sorts():
    existing = {
        "2026-05-09T00:00:00Z": json.dumps({"taken_at": "2026-05-09T00:00:00Z", "payload": {"old": 1}}),
    }
    new_rows = [
        ("2026-05-03T00:00:00Z", '{"new": 2}'),
        ("2026-05-09T00:00:00Z", '{"new": 3}'),  # conflict: new wins
    ]
    merged = arch.merge_partition_bytes(existing, new_rows).decode()
    lines = [json.loads(l) for l in merged.strip().split("\n")]
    # Sorted by taken_at.
    assert [l["taken_at"] for l in lines] == ["2026-05-03T00:00:00Z", "2026-05-09T00:00:00Z"]
    # New row won the conflict.
    assert lines[1]["payload"] == {"new": 3}


def test_merge_partition_empty():
    assert arch.merge_partition_bytes({}, []) == b""


def test_s3_config_none_when_missing():
    assert arch.s3_config_from_env({}) is None
    assert arch.s3_config_from_env({"RADON_ARCHIVE_S3_ENDPOINT": "x"}) is None


def test_s3_config_complete_with_defaults():
    cfg = arch.s3_config_from_env(
        {
            "RADON_ARCHIVE_S3_ENDPOINT": "https://acct.r2.cloudflarestorage.com",
            "RADON_ARCHIVE_S3_BUCKET": "radon-archive",
            "RADON_ARCHIVE_S3_ACCESS_KEY_ID": "AK",
            "RADON_ARCHIVE_S3_SECRET_ACCESS_KEY": "SK",
        }
    )
    assert cfg["bucket"] == "radon-archive"
    assert cfg["region"] == "auto"
    assert cfg["prefix"] == "portfolio_snapshots/"


# --------------------------------------------------------------------------
# Integration: fetch_archivable_rows + run_archive over an in-memory sqlite
# --------------------------------------------------------------------------
@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE portfolio_snapshots (taken_at TEXT PRIMARY KEY, payload TEXT)"
    )
    rows = [
        ("2026-05-03T00:00:00.000000Z", '{"nav": 1}'),
        ("2026-05-20T00:00:00.000000Z", '{"nav": 2}'),
        ("2026-06-01T00:00:00.000000Z", '{"nav": 3}'),
        ("2026-06-27T00:00:00.000000Z", '{"nav": 4}'),  # recent, inside window
    ]
    conn.executemany("INSERT INTO portfolio_snapshots VALUES (?, ?)", rows)
    conn.commit()
    return conn


def _delete_fn(conn):
    def delete(cutoff: str) -> int:
        cur = conn.execute("DELETE FROM portfolio_snapshots WHERE taken_at < ?", (cutoff,))
        conn.commit()
        return cur.rowcount

    return delete


def test_fetch_archivable_rows_keyset_pages(db):
    cutoff = "2026-06-26T00:00:00.000000Z"
    rows = list(arch.fetch_archivable_rows(db, cutoff, batch_size=2))
    taken_ats = [r[0] for r in rows]
    assert taken_ats == [
        "2026-05-03T00:00:00.000000Z",
        "2026-05-20T00:00:00.000000Z",
        "2026-06-01T00:00:00.000000Z",
    ]


def test_run_archive_no_rows_returns_zeros(db, tmp_path):
    summary = arch.run_archive(
        db,
        cutoff="2026-01-01T00:00:00.000000Z",
        archive_dir=tmp_path,
        batch_size=500,
        s3_cfg=None,
        do_delete=True,
        allow_delete_without_upload=True,
        delete_fn=_delete_fn(db),
    )
    assert summary["archived"] == 0
    assert summary["deleted"] == 0


def test_run_archive_writes_partitions_and_deletes(db, tmp_path):
    cutoff = "2026-06-26T00:00:00.000000Z"
    summary = arch.run_archive(
        db,
        cutoff=cutoff,
        archive_dir=tmp_path,
        batch_size=500,
        s3_cfg=None,
        do_delete=True,
        allow_delete_without_upload=True,
        delete_fn=_delete_fn(db),
    )
    assert summary["archived"] == 3
    assert summary["months"] == ["2026-05", "2026-06"]
    assert summary["deleted"] == 3
    # Files exist + contain the archived rows.
    assert arch.read_partition_rows(tmp_path / "2026-05.jsonl.gz").keys() == {
        "2026-05-03T00:00:00.000000Z",
        "2026-05-20T00:00:00.000000Z",
    }
    # Only the recent row survives in the DB.
    remaining = db.execute("SELECT taken_at FROM portfolio_snapshots").fetchall()
    assert remaining == [("2026-06-27T00:00:00.000000Z",)]


def test_run_archive_refuses_delete_without_upload(db, tmp_path):
    with pytest.raises(RuntimeError, match="off-box upload not configured"):
        arch.run_archive(
            db,
            cutoff="2026-06-26T00:00:00.000000Z",
            archive_dir=tmp_path,
            batch_size=500,
            s3_cfg=None,
            do_delete=True,
            allow_delete_without_upload=False,
            delete_fn=_delete_fn(db),
        )
    # Nothing deleted on refusal.
    assert db.execute("SELECT COUNT(*) FROM portfolio_snapshots").fetchone()[0] == 4


def test_run_archive_no_delete_keeps_rows(db, tmp_path):
    summary = arch.run_archive(
        db,
        cutoff="2026-06-26T00:00:00.000000Z",
        archive_dir=tmp_path,
        batch_size=500,
        s3_cfg=None,
        do_delete=False,
        allow_delete_without_upload=False,
        delete_fn=_delete_fn(db),
    )
    assert summary["archived"] == 3
    assert summary["deleted"] == 0
    assert db.execute("SELECT COUNT(*) FROM portfolio_snapshots").fetchone()[0] == 4


def test_run_archive_uploads_each_month(db, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(arch, "upload_partition", lambda path, key, cfg: calls.append(key))
    cfg = {
        "endpoint_url": "https://x",
        "bucket": "b",
        "access_key": "a",
        "secret_key": "s",
        "region": "auto",
        "prefix": "portfolio_snapshots/",
    }
    summary = arch.run_archive(
        db,
        cutoff="2026-06-26T00:00:00.000000Z",
        archive_dir=tmp_path,
        batch_size=500,
        s3_cfg=cfg,
        do_delete=True,
        allow_delete_without_upload=False,
        delete_fn=_delete_fn(db),
    )
    assert summary["uploaded"] == 2
    assert calls == [
        "portfolio_snapshots/2026-05.jsonl.gz",
        "portfolio_snapshots/2026-06.jsonl.gz",
    ]
    assert summary["deleted"] == 3


def test_run_archive_is_idempotent_dedup(db, tmp_path):
    cutoff = "2026-06-26T00:00:00.000000Z"
    common = dict(
        archive_dir=tmp_path,
        batch_size=500,
        s3_cfg=None,
        do_delete=False,
        allow_delete_without_upload=False,
        delete_fn=_delete_fn(db),
    )
    arch.run_archive(db, cutoff=cutoff, **common)
    # Re-run: same rows still in DB (no delete), archive must not duplicate.
    arch.run_archive(db, cutoff=cutoff, **common)
    may = arch.read_partition_rows(tmp_path / "2026-05.jsonl.gz")
    assert len(may) == 2  # not 4
