"""Every reviewed document mirrors its outcome and reason codes to Turso for the operator's Held review."""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from research import intake, publish
from tests.test_research_intake import Reviewer, catalogue, extractor, selection, verdict, work  # synthetic fixtures


def review(**overrides):
    value = {"pipeline": "v2", "outcome": "reviewed", "identity": {"publisher": "Goldman Sachs", "series": "tic data", "doc_type": "research",
             "date": "2026-09-16", "date_source": "text"},
             "audit": [{"held": "NUMBER_NOT_ON_PAGE", "claim_key": "tic-july", "missing": [{"token": "$47bn", "kind": "number"}]},
                       {"held": "VERIFY_FAILED", "claim_key": "tic-aug", "verification": {"reason": "covered last week"}}],
             "selection": {"candidates": [{"claim_key": "tic-july", "title": "Foreign investors bought $47bn", "content": "Body A " * 200},
                                          {"claim_key": "tic-aug", "title": "August flows", "content": "Body B"}]},
             "posts": []}
    value.update(overrides)
    return value


def test_advisory_grounding_without_held_contributes_no_reason_code():
    row = publish.outcome_row(work(), review(
        audit=[{"claim_key": "tic-july", "grounding": [{"token": "$47bn", "page": None}],
                "unmatched": ["$47bn"]}],
        posts=[{"id": "research-1"}],
    ))
    assert row["outcome"] == "published"
    assert json.loads(row["reason_codes"]) == []
    assert json.loads(row["drafts_json"]) == []


def test_outcome_row_carries_reason_codes_and_rejected_drafts():
    row = publish.outcome_row(work(), review())
    assert row["work_key"] == "k" * 64 and row["file_id"] == "id:one" and row["file_name"] == "tic data.pdf"
    assert (row["publisher"], row["series"], row["doc_type"], row["folder_date"], row["document_date"]) == \
        ("Goldman Sachs", "tic data", "research", "2026-09-17", "2026-09-16")
    assert row["outcome"] == "held" and row["posts"] == 0 and row["pipeline"] == "v2"
    assert json.loads(row["reason_codes"]) == ["NUMBER_NOT_ON_PAGE", "VERIFY_FAILED"]
    drafts = json.loads(row["drafts_json"])
    assert drafts[0] == {"title": "Foreign investors bought $47bn", "content": ("Body A " * 200).strip()[:600], "held": "NUMBER_NOT_ON_PAGE", "detail": "$47bn"}
    assert drafts[1]["held"] == "VERIFY_FAILED" and drafts[1]["detail"] == "covered last week"


@pytest.mark.parametrize("overrides,outcome,codes", [
    ({"outcome": "dropped", "reason_code": "DOC_TYPE_FX_PAIR_NOTE", "audit": [], "selection": None}, "dropped", ["DOC_TYPE_FX_PAIR_NOTE"]),
    ({"audit": [], "selection": {"candidates": []}}, "held", ["NO_CANDIDATES"]),
    ({"posts": [{"id": "research-1"}], "audit": []}, "published", []),
])
def test_outcome_classification(overrides, outcome, codes):
    row = publish.outcome_row(work(), review(**overrides))
    assert row["outcome"] == outcome and json.loads(row["reason_codes"]) == codes


def test_record_outcome_upserts_by_work_key(monkeypatch):
    calls = []
    monkeypatch.setattr(publish, "hrana_execute", lambda sql, args=(), **kw: calls.append((sql, args)) or [])
    publish.record_outcome(work(), review())
    sql, args = calls[0]
    assert "INSERT INTO research_outcomes" in sql and "ON CONFLICT(work_key) DO UPDATE" in sql
    assert args[0] == "k" * 64 and "held" in args
    assert "held_at" in sql and "COALESCE(research_outcomes.held_at, excluded.held_at)" in sql
    assert args[-1] == args[-2] and args[-1]  # held row: held_at = updated_at = now


def test_record_outcome_keeps_held_at_on_reupsert_of_the_same_held_row(monkeypatch):
    calls = []
    monkeypatch.setattr(publish, "hrana_execute", lambda sql, args=(), **kw: calls.append((sql, args)) or [])
    publish.record_outcome(work(), review())
    publish.record_outcome(work(), review())
    first, second = calls[0][1][-1], calls[1][1][-1]
    assert first and second
    sql = calls[0][0]
    assert "COALESCE(research_outcomes.held_at, excluded.held_at)" in sql
    publish.record_outcome(work(), review(posts=[{"id": "research-1"}], audit=[]))
    published_sql, published_args = calls[2]
    assert published_args[-1] is None
    assert "ELSE research_outcomes.held_at END" in published_sql


def test_intake_records_every_outcome_and_survives_a_mirror_failure(tmp_path):
    recorded = []
    publisher = SimpleNamespace(store_asset=lambda p: "/api/newsfeed/research/files/" + "b" * 64 + "." + str(p).rsplit(".", 1)[-1],
                                record_outcome=lambda w, r: recorded.append((w["key"], r["outcome"])))
    pipe = intake.Pipeline(tmp_path, Reviewer([selection(), verdict()]), publisher, extractor=extractor, figure_catalogue=catalogue, pdf_created=lambda pdf: None)
    assert len(pipe.process(work(), tmp_path / "r.pdf", [])) == 1
    dropped = dict(work("gbpusd_en_1666701.pdf"), key="d" * 64)
    assert pipe.process(dropped, tmp_path / "r.pdf", []) == []
    assert recorded == [("k" * 64, "reviewed"), ("d" * 64, "dropped")]

    def broken(w, r):
        raise RuntimeError("turso down")
    publisher.record_outcome = broken
    again = dict(work("other note.pdf"), key="e" * 64)
    again["metadata"] = dict(again["metadata"], id="id:three")
    pipe = intake.Pipeline(tmp_path / "second", Reviewer([selection(), verdict()]), publisher, extractor=extractor, figure_catalogue=catalogue, pdf_created=lambda pdf: None)
    assert len(pipe.process(again, tmp_path / "r.pdf", [])) == 1, "the mirror is telemetry; it never fails a document"


def test_outcome_row_carries_enough_context_to_judge_a_document_with_no_drafts():
    context = json.loads(publish.outcome_row(work(), review(
        audit=[], selection={"candidates": [], "reason": "Historical essay on doomsday predictions; no measured market finding."},
        figures=[{"id": "f1"}, {"id": "f2"}],
        document={"page_count": 9, "excerpt": "The Daily Froth: A Brief History of the End of the World. History offers some reassurance.", "source_url": "/api/newsfeed/research/files/" + "c" * 64 + ".pdf"},
    ))["context_json"])
    assert context == {"pageCount": 9, "figureCount": 2, "dateSource": "text",
                       "excerpt": "The Daily Froth: A Brief History of the End of the World. History offers some reassurance.",
                       "selectorReason": "Historical essay on doomsday predictions; no measured market finding.",
                       "sourceUrl": "/api/newsfeed/research/files/" + "c" * 64 + ".pdf"}


def test_outcome_row_surfaces_text_only_with_figures_and_figure_count():
    row = publish.outcome_row(work(), review(
        audit=[{"held": "TEXT_ONLY_WITH_FIGURES", "claim_key": "tic-july", "figures_on_cited_pages": ["f1"]}],
        selection={"candidates": [{"claim_key": "tic-july", "title": "Foreign investors bought $45bn", "content": "Body"}],
                   "reason": "measured flow"},
        figures=[{"id": "f1"}],
        posts=[],
    ))
    assert json.loads(row["reason_codes"]) == ["TEXT_ONLY_WITH_FIGURES"]
    assert json.loads(row["context_json"])["figureCount"] > 0


def test_held_cutoff_is_updated_at_minus_24h_against_the_pt_clock():
    now = datetime(2026, 9, 19, 15, 0, tzinfo=timezone.utc)
    assert publish.held_cutoff(now) == datetime(2026, 9, 18, 15, 0, tzinfo=timezone.utc)


def test_expire_stale_held_updates_held_rows_and_never_publishes(monkeypatch):
    calls = []
    def execute(sql, args=(), **kw):
        calls.append((sql, args))
        if sql.lstrip().startswith("SELECT"):
            return [(1, "old-key", "Goldman Sachs", "tic data", "tic data.pdf", "{}")]
        return [("old-key",)]
    monkeypatch.setattr(publish, "hrana_execute", execute)
    published = []
    monkeypatch.setattr(publish, "publish", lambda post: published.append(post))
    count = publish.expire_stale_held(now=datetime(2026, 9, 19, 15, 0, tzinfo=timezone.utc))
    assert any("SELECT" in sql for sql, _ in calls)
    update = next((sql, args) for sql, args in calls if "HELD_EXPIRED" in sql)
    sql, args = update
    assert "folder_date" not in sql
    assert "HELD_EXPIRED" in sql and "outcome = 'held'" in sql
    assert "expired_at = ?" in sql
    assert "updated_at = ?" not in sql
    assert "COALESCE(held_at, updated_at)" in sql
    assert any("2026-09-18T15:00:00" in str(a) for a in args)
    assert count == 1 and published == []
    assert publish.HELD_TTL_HOURS == 24


def _outcome_db():
    connection = sqlite3.connect(":memory:")
    root = Path(__file__).resolve().parents[1] / "db/migrations"
    connection.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
    connection.executescript((root / "0079_research_outcomes.sql").read_text())
    connection.executescript((root / "0091_research_outcomes_hold_times.sql").read_text())
    return connection


def test_expire_sql_merges_held_expired_on_sqlite():
    connection = _outcome_db()
    cols = ("work_key,file_id,file_name,publisher,series,doc_type,folder_date,document_date,"
            "outcome,reason_codes,drafts_json,posts,pipeline,updated_at")
    rows = [
        ("old", "id:old", "old.pdf", "GS", "x", "research", "2026-09-01", "2026-09-01",
         "held", '["NO_CANDIDATES"]', "[]", 0, "v2", "2026-09-17T10:00:00+00:00"),
        ("fresh", "id:fresh", "fresh.pdf", "GS", "x", "research", "2026-09-19", "2026-09-19",
         "held", '["VERIFY_FAILED"]', "[]", 0, "v2", "2026-09-19T10:00:00+00:00"),
        ("pub", "id:pub", "pub.pdf", "GS", "x", "research", "2026-09-17", "2026-09-17",
         "published", "[]", "[]", 1, "v2", "2026-09-17T10:00:00+00:00"),
    ]
    connection.executemany(f"INSERT INTO research_outcomes ({cols}) VALUES ({','.join('?' * 14)})", rows)
    stamped = connection.execute(publish._EXPIRE_SQL, ("2026-09-19T15:00:00+00:00", "2026-09-18T15:00:00+00:00")).fetchall()
    assert [row[0] for row in stamped] == ["old"]
    old = connection.execute(
        "SELECT outcome, reason_codes, updated_at, expired_at FROM research_outcomes WHERE work_key='old'"
    ).fetchone()
    assert old[0] == "dropped" and old[1] == '["NO_CANDIDATES","HELD_EXPIRED"]'
    assert old[2] == "2026-09-17T10:00:00+00:00"
    assert old[3] == "2026-09-19T15:00:00+00:00"
    assert connection.execute("SELECT outcome FROM research_outcomes WHERE work_key='fresh'").fetchone()[0] == "held"
    assert connection.execute("SELECT outcome FROM research_outcomes WHERE work_key='pub'").fetchone()[0] == "published"
    connection.close()


def test_expire_sql_uses_held_at_when_present_and_leaves_updated_at():
    connection = _outcome_db()
    cols = ("work_key,file_id,file_name,publisher,series,doc_type,folder_date,document_date,"
            "outcome,reason_codes,drafts_json,posts,pipeline,updated_at,held_at")
    rows = [
        ("aged-hold", "id:a", "a.pdf", "GS", "x", "research", "2026-09-01", "2026-09-01",
         "held", '["NO_CANDIDATES"]', "[]", 0, "v2",
         "2026-09-19T14:00:00+00:00", "2026-09-17T10:00:00+00:00"),
        ("fresh-hold", "id:b", "b.pdf", "GS", "x", "research", "2026-09-18", "2026-09-18",
         "held", '["VERIFY_FAILED"]', "[]", 0, "v2",
         "2026-09-17T10:00:00+00:00", "2026-09-19T14:00:00+00:00"),
    ]
    connection.executemany(f"INSERT INTO research_outcomes ({cols}) VALUES ({','.join('?' * 15)})", rows)
    stamped = connection.execute(publish._EXPIRE_SQL, ("2026-09-19T15:00:00+00:00", "2026-09-18T15:00:00+00:00")).fetchall()
    assert [row[0] for row in stamped] == ["aged-hold"]
    aged = connection.execute(
        "SELECT outcome, updated_at, expired_at, held_at FROM research_outcomes WHERE work_key='aged-hold'"
    ).fetchone()
    assert aged == ("dropped", "2026-09-19T14:00:00+00:00", "2026-09-19T15:00:00+00:00", "2026-09-17T10:00:00+00:00")
    fresh = connection.execute(
        "SELECT outcome, updated_at, expired_at FROM research_outcomes WHERE work_key='fresh-hold'"
    ).fetchone()
    assert fresh == ("held", "2026-09-17T10:00:00+00:00", None)
    connection.close()


def test_intake_records_the_opening_text_and_a_pdf_link_for_a_document_that_publishes_nothing(tmp_path):
    seen = []
    publisher = SimpleNamespace(store_asset=lambda p: "/api/newsfeed/research/files/" + "c" * 64 + "." + str(p).rsplit(".", 1)[-1],
                                record_outcome=lambda w, r: seen.append(r))
    pipe = intake.Pipeline(tmp_path, Reviewer([{"candidates": [], "reason": "no measured finding"}]), publisher, extractor=extractor, figure_catalogue=catalogue, pdf_created=lambda pdf: None)
    pdf = tmp_path / "r.pdf"; pdf.write_bytes(b"%PDF-1.4")
    assert pipe.process(work(), pdf, []) == []
    document = seen[0]["document"]
    assert document["page_count"] == 2 and document["excerpt"].startswith("## Economics Research ## 16 September 2026")
    assert len(document["excerpt"]) <= 900 and document["source_url"].endswith(".pdf")


@pytest.mark.parametrize("protected_context", [
    '{"alwaysPublish":true}',
    '{"excerpt":"Scott Rubner. Citadel Securities."}',
    '{}',
    '[]',
    'not-json',
])
def test_expiry_executes_real_sql_without_dropping_protected_or_fresh_rows(monkeypatch, protected_context):
    """T-532: a mixed sweep must preserve protected rows at the write boundary."""
    connection = _outcome_db()
    connection.execute("ALTER TABLE research_outcomes ADD COLUMN context_json TEXT NOT NULL DEFAULT '{}'")
    old = "2026-10-01T10:00:00+00:00"
    fresh = "2026-10-04T14:00:00+00:00"
    columns = "work_key,file_name,publisher,series,outcome,reason_codes,updated_at,held_at,context_json"
    rows = [
        ("old", "ordinary.pdf", "Goldman Sachs", "ordinary", "held", '["VERIFY_FAILED"]', fresh, old, "{}"),
        ("legacy", "legacy.pdf", "Goldman Sachs", "ordinary", "held", '["HELD_EXPIRED"]', old, None, "{}"),
        ("protected", "Citadel Rubner GMI.pdf", "Citadel", "GMI", "held", '["VERIFY_FAILED"]', old, old, protected_context),
        ("flag-only", "other.pdf", "unknown", "other", "held", "[]", old, old, '{"alwaysPublish":true}'),
        ("excerpt-only", "other.pdf", "unknown", "other", "held", "[]", old, old,
         '{"excerpt":"Scott Rubner. Citadel Securities."}'),
        ("fresh", "fresh.pdf", "Goldman Sachs", "ordinary", "held", "[]", old, fresh, "{}"),
        ("boundary", "boundary.pdf", "Goldman Sachs", "ordinary", "held", "[]", old, "2026-10-03T15:00:00+00:00", "{}"),
        ("published", "published.pdf", "Goldman Sachs", "ordinary", "published", "[]", old, old, "{}"),
        ("dropped", "dropped.pdf", "Goldman Sachs", "ordinary", "dropped", "[]", old, old, "{}"),
    ]
    connection.executemany(f"INSERT INTO research_outcomes ({columns}) VALUES ({','.join('?' * 9)})", rows)
    before = dict(connection.execute("SELECT work_key, json_array(outcome,reason_codes,updated_at,held_at,expired_at) FROM research_outcomes"))
    calls = []

    def execute(sql, args=(), **kwargs):
        calls.append(sql)
        return connection.execute(sql, args).fetchall()

    monkeypatch.setattr(publish, "hrana_execute", execute)
    monkeypatch.setattr(publish, "publish", lambda _: pytest.fail("expiry must not publish"))
    now = datetime(2026, 10, 4, 15, 0, tzinfo=timezone.utc)
    try:
        assert publish.expire_stale_held(now=now) == 2
        assert len(calls) == 2
        for key in ("old", "legacy"):
            result = connection.execute(
                "SELECT outcome,reason_codes,updated_at,held_at,expired_at FROM research_outcomes WHERE work_key=?", (key,)
            ).fetchone()
            assert result[0] == "dropped"
            assert json.loads(result[1]).count("HELD_EXPIRED") == 1
            assert result[2:4] == ((fresh, old) if key == "old" else (old, None))
            assert datetime.fromisoformat(result[4]).tzinfo is not None
        after = dict(connection.execute("SELECT work_key, json_array(outcome,reason_codes,updated_at,held_at,expired_at) FROM research_outcomes"))
        assert {key for key in before if before[key] != after[key]} == {"old", "legacy"}
        assert publish.expire_stale_held(now=now) == 0
        assert len(calls) == 3  # No second write when only protected holds remain.
    finally:
        connection.close()


def _rel307_store(count=801):
    connection = _outcome_db()
    connection.execute("ALTER TABLE research_outcomes ADD COLUMN context_json TEXT NOT NULL DEFAULT '{}'")
    connection.executemany(
        "INSERT INTO research_outcomes (work_key, publisher, outcome, updated_at, context_json) VALUES (?, ?, ?, ?, ?)",
        [(f"z-{i:05}", "Goldman Sachs", "held", "2026-09-01T00:00:00+00:00",
          json.dumps({"alwaysPublish": i % 200 == 0, "excerpt": "synthetic research text"}))
         for i in range(count)],
    )
    return connection


@pytest.mark.parametrize("fault", ["response-cap", "parameter-cap"])
def test_rel307_large_held_history_expires_in_bounded_pages(monkeypatch, fault):
    """R-726 / REL-307: growing outcomes must not poison TTL forever."""
    from api.db_http import DbHttpError
    connection = _rel307_store()
    connection.executemany(
        "INSERT INTO research_outcomes (work_key, outcome, updated_at) VALUES (?, ?, ?)",
        [("fresh", "held", "2026-10-04T00:00:00+00:00"),
         ("published", "published", "2026-09-01T00:00:00+00:00")],
    )
    if fault == "parameter-cap":
        connection.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 256)
    calls = []
    def execute(sql, args=(), **kwargs):
        rows = connection.execute(sql, args).fetchall()
        calls.append((sql, args, len(rows)))
        if fault == "response-cap" and sql.lstrip().startswith("SELECT") and len(rows) > 200:
            raise DbHttpError("response exceeds transport budget")
        return rows
    monkeypatch.setattr(publish, "hrana_execute", execute)
    try:
        assert publish.expire_stale_held(now=datetime(2026, 10, 4, tzinfo=timezone.utc)) == 796
        assert connection.execute("SELECT count(*) FROM research_outcomes WHERE outcome='held'").fetchone() == (6,)
        assert connection.execute("SELECT outcome, expired_at FROM research_outcomes WHERE work_key='published'").fetchone() == ("published", None)
        assert connection.execute("SELECT outcome, expired_at FROM research_outcomes WHERE work_key='fresh'").fetchone() == ("held", None)
        assert connection.execute("SELECT count(*) FROM research_outcomes WHERE work_key LIKE 'z-%' AND updated_at='2026-09-01T00:00:00+00:00'").fetchone() == (801,)
        assert max(len(args) for _, args, _ in calls) <= 202
        assert max(n for sql, _, n in calls if sql.lstrip().startswith("SELECT")) <= 200
        assert publish.expire_stale_held(now=datetime(2026, 10, 4, tzinfo=timezone.utc)) == 0
        assert connection.execute("SELECT count(*) FROM research_outcomes WHERE reason_codes=?", ('["HELD_EXPIRED"]',)).fetchone() == (796,)
    finally:
        connection.close()


def test_rel307_total_deadline_prevents_writes_after_a_stalled_scan(monkeypatch):
    connection = _rel307_store(100)
    elapsed = [0.0]
    monkeypatch.setattr(publish, "time", SimpleNamespace(monotonic=lambda: elapsed[0]), raising=False)
    def stalled(sql, args=(), **kwargs):
        rows = connection.execute(sql, args).fetchall()
        if sql.lstrip().startswith("SELECT"):
            elapsed[0] = 1000.0
        return rows
    monkeypatch.setattr(publish, "hrana_execute", stalled)
    try:
        with pytest.raises(TimeoutError, match="held.*deadline"):
            publish.expire_stale_held(now=datetime(2026, 10, 4, tzinfo=timezone.utc))
        assert connection.execute("SELECT count(*) FROM research_outcomes WHERE outcome='held'").fetchone() == (100,)
    finally:
        connection.close()


def test_rel307_later_page_failure_replays_completed_progress_safely(monkeypatch):
    from api.db_http import DbHttpError
    connection = _rel307_store(401)
    reads = [0]
    def fail_second(sql, args=(), **kwargs):
        if sql.lstrip().startswith("SELECT"):
            reads[0] += 1
            if reads[0] == 2:
                raise DbHttpError("page unavailable")
        return connection.execute(sql, args).fetchall()
    monkeypatch.setattr(publish, "hrana_execute", fail_second)
    try:
        with pytest.raises(DbHttpError, match="page unavailable"):
            publish.expire_stale_held(now=datetime(2026, 10, 4, tzinfo=timezone.utc))
        assert connection.execute("SELECT count(*) FROM research_outcomes WHERE outcome='dropped'").fetchone() == (199,)
        monkeypatch.setattr(publish, "hrana_execute", lambda sql, args=(), **kw: connection.execute(sql, args).fetchall())
        assert publish.expire_stale_held(now=datetime(2026, 10, 4, tzinfo=timezone.utc)) == 199
        assert connection.execute("SELECT count(*) FROM research_outcomes WHERE reason_codes=?", ('["HELD_EXPIRED"]',)).fetchone() == (398,)
    finally:
        connection.close()
