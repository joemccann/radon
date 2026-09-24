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
    monkeypatch.setattr(publish, "hrana_execute", lambda sql, args=(), **kw: calls.append((sql, args)) or [("old-key",)])
    published = []
    monkeypatch.setattr(publish, "publish", lambda post: published.append(post))
    count = publish.expire_stale_held(now=datetime(2026, 9, 19, 15, 0, tzinfo=timezone.utc))
    sql, args = calls[0]
    assert sql == publish._EXPIRE_SQL
    assert "folder_date" not in sql
    assert "HELD_EXPIRED" in sql and "outcome = 'held'" in sql
    assert any("2026-09-18T15:00:00" in str(a) for a in args)
    assert count == 1 and published == []


def test_expire_sql_merges_held_expired_on_sqlite():
    connection = sqlite3.connect(":memory:")
    root = Path(__file__).resolve().parents[1] / "db/migrations"
    connection.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
    connection.executescript((root / "0079_research_outcomes.sql").read_text())
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
    old = connection.execute("SELECT outcome, reason_codes FROM research_outcomes WHERE work_key='old'").fetchone()
    assert old == ("dropped", '["NO_CANDIDATES","HELD_EXPIRED"]')
    assert connection.execute("SELECT outcome FROM research_outcomes WHERE work_key='fresh'").fetchone()[0] == "held"
    assert connection.execute("SELECT outcome FROM research_outcomes WHERE work_key='pub'").fetchone()[0] == "published"
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
