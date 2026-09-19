"""Every reviewed document mirrors its outcome and reason codes to Turso for the operator's Held review."""
import json
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
