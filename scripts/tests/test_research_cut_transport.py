"""T-517: exercise the daily cut's real query and wire boundary without Turso."""
from __future__ import annotations

import io
import json
import sqlite3
from types import SimpleNamespace

import pytest

from research import cut_report


@pytest.fixture
def wire(monkeypatch):
    db = sqlite3.connect(":memory:")
    db.execute("""CREATE TABLE research_outcomes (
        outcome TEXT, folder_date TEXT, file_name TEXT, publisher TEXT,
        series TEXT, posts INTEGER, reason_codes TEXT, work_key TEXT,
        updated_at TEXT, document_date TEXT, held_at TEXT, expired_at TEXT
    )""")
    calls = []
    failures = []

    def open_request(request, timeout):
        body = json.loads(request.data)
        calls.append((request, timeout, body))
        assert request.full_url == "https://cut.invalid/v2/pipeline"
        assert request.get_method() == "POST"
        assert request.get_header("Authorization") == "Bearer synthetic-cut-token"
        assert request.get_header("Content-type") == "application/json"
        assert timeout == 90
        assert [part["type"] for part in body["requests"]] == ["execute", "close"]
        if failures:
            failure = failures.pop(0)
            if isinstance(failure, Exception):
                raise failure
            result = {"type": "error", "error": {"message": failure}}
        else:
            stmt = body["requests"][0]["stmt"]
            # SQLite enforces read-only execution, rather than grepping SQL text.
            db.execute("PRAGMA query_only=ON")
            try:
                cursor = db.execute(stmt["sql"], [cell.get("value") for cell in stmt["args"]])
                rows = cursor.fetchall()
                cols = [{"name": col[0]} for col in cursor.description]
                result = {"type": "ok", "response": {"result": {
                    "cols": cols,
                    "rows": [[{"type": "null"} if value is None else {
                        "type": "integer" if isinstance(value, int) else "text",
                        "value": str(value),
                    } for value in row] for row in rows],
                }}}
            except sqlite3.Error as exc:
                result = {"type": "error", "error": {"message": str(exc)}}
        return io.BytesIO(json.dumps({"results": [result, {"type": "ok"}]}).encode())

    monkeypatch.setattr(cut_report.urllib.request, "urlopen", open_request)
    monkeypatch.setenv("TURSO_DB_URL", "libsql://cut.invalid/")
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "synthetic-cut-token")
    yield SimpleNamespace(db=db, calls=calls, failures=failures)
    db.close()


@pytest.mark.parametrize("date,start,end", [
    ("2026-09-28", "2026-09-28T07:00:00.000Z", "2026-09-29T07:00:00.000Z"),
    ("2026-03-08", "2026-03-08T08:00:00.000Z", "2026-03-09T07:00:00.000Z"),
    ("2026-11-01", "2026-11-01T07:00:00.000Z", "2026-11-02T08:00:00.000Z"),
])
def test_query_selects_each_timestamp_at_pt_day_boundaries(wire, date, start, end):
    for column in ("updated_at", "held_at", "expired_at"):
        for label, timestamp in (("start", start), ("end", end), ("old", "2025-01-01T00:00:00Z")):
            wire.db.execute(
                f"INSERT INTO research_outcomes (work_key, {column}) VALUES (?, ?)",
                (f"{column}-{label}", timestamp),
            )
    rows = cut_report.fetch_rows(date)
    assert {row["work_key"] for row in rows} == {
        "updated_at-start", "held_at-start", "expired_at-start",
    }
    assert len(wire.calls) == 1
    args = wire.calls[0][2]["requests"][0]["stmt"]["args"]
    assert args == [{"type": "text", "value": value} for value in (start, end) * 3]
    assert all(row["file_name"] is None and row["posts"] == "0" for row in rows)


def test_cli_reads_wire_rows_and_writes_matching_reports(wire, tmp_path, capsys):
    wire.db.execute("""INSERT INTO research_outcomes
        (outcome, folder_date, file_name, posts, work_key, updated_at)
        VALUES ('published', '2026-09-28', 'sample.pdf', 3, 'sample', '2026-09-28T18:00:00Z')""")
    json_out, md_out = tmp_path / "cut.json", tmp_path / "cut.md"
    assert cut_report.main([
        "--date", "2026-09-28", "--json-out", str(json_out), "--md-out", str(md_out),
    ]) == 0
    assert json.loads(json_out.read_text())["buckets"]["published"]["posts"] == 3
    assert "published: 1 docs, 3 posts" in md_out.read_text()
    assert json.loads(capsys.readouterr().out)["det_n"] == 1
    assert len(wire.calls) == 1


def test_legacy_schema_retries_read_only_query_without_hold_columns(wire):
    wire.db.execute("ALTER TABLE research_outcomes DROP COLUMN held_at")
    wire.db.execute("ALTER TABLE research_outcomes DROP COLUMN expired_at")
    wire.db.execute("""INSERT INTO research_outcomes (work_key, updated_at)
        VALUES ('legacy', '2026-09-28T18:00:00Z')""")
    rows = cut_report.fetch_rows("2026-09-28")
    assert [row["work_key"] for row in rows] == ["legacy"]
    assert "held_at" not in rows[0] and "expired_at" not in rows[0]
    assert len(wire.calls) == 2
    args = wire.calls[1][2]["requests"][0]["stmt"]["args"]
    assert [arg["value"] for arg in args] == [
        "2026-09-28T07:00:00.000Z", "2026-09-29T07:00:00.000Z",
    ]


@pytest.mark.parametrize("failure,error", [
    ("permission denied", RuntimeError),
    (TimeoutError("injected transport timeout"), TimeoutError),
])
def test_non_schema_failure_propagates_without_retry_or_empty_report(wire, failure, error):
    wire.failures.append(failure)
    with pytest.raises(error, match="permission denied|injected transport timeout"):
        cut_report.fetch_rows("2026-09-28")
    assert len(wire.calls) == 1


@pytest.mark.parametrize("missing", ["TURSO_DB_URL", "TURSO_AUTH_TOKEN"])
def test_missing_configuration_fails_before_opening_transport(wire, monkeypatch, missing):
    monkeypatch.delenv(missing)
    with pytest.raises(SystemExit, match="required for a live cut"):
        cut_report.fetch_rows("2026-09-28")
    assert wire.calls == []
