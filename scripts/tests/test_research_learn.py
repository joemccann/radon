"""Learning from operator votes: selector examples are automatic; hard rules are only ever proposed."""
import json
from types import SimpleNamespace

import pytest

from research import identify, intake, learn, triage
from tests.test_research_intake import Reviewer, catalogue, extractor, selection, verdict, work


def vote(id_, target, vote_, snapshot, *, reasons=(), comment="", key=None, created="2026-09-19T12:00:00Z"):
    post_id = key if target == "post" else None
    work_key = key if target == "held" else None
    return (id_, target, post_id, work_key, "id:" + id_, vote_, json.dumps(list(reasons)), comment, json.dumps(snapshot), created)


SINGLE_STOCK = {"title": "Bloomberg estimates use only 5 out of 19 analysts on Avolta", "tags": ["EQUITIES", "CONSENSUS"], "publisher": "unknown", "series": "avolta", "docType": "single_stock"}
FOMC = {"title": "FOMC hikes 25 bps to 3.75%-4.00%", "tags": ["MACRO", "RATES"], "publisher": "PNC Economics", "series": "pnc economics research fomc statement", "docType": "research"}


def rows():
    out = [vote("u1", "post", "up", FOMC, key="research-1", comment="rates decisions always matter"),
           vote("d1", "post", "down", SINGLE_STOCK, reasons=["not_relevant"], key="research-2", comment="single stock, not for me")]
    for i in range(3):
        out.append(vote(f"s{i}", "post", "down", {"title": f"USDCHF view {i}", "publisher": "UBS", "series": "ubs cio fx view", "docType": "research", "tags": ["FX"]},
                        reasons=["not_relevant"], key=f"research-fx{i}"))
    out.append(vote("h1", "held", "down", {"fileName": "ubs cio fx view.pdf", "publisher": "UBS", "series": "ubs cio fx view", "docType": "research", "reasonCodes": ["NO_CANDIDATES"]}, key="a" * 64))
    out.append(vote("old", "post", "down", FOMC, key="research-1", created="2026-09-18T12:00:00Z"))   # superseded by u1
    return out


def test_examples_are_the_latest_vote_per_item_with_the_operators_words():
    examples = learn.examples(rows())
    by_id = {e["id"]: e for e in examples}
    assert "old" not in by_id and by_id["u1"]["verdict"] == "wanted" and by_id["d1"]["verdict"] == "not wanted"
    assert by_id["d1"]["reasons"] == ["not_relevant"] and by_id["d1"]["comment"] == "single stock, not for me"
    assert by_id["h1"]["verdict"] == "correctly held"


def test_the_most_similar_examples_are_chosen_for_a_document():
    facts = {"publisher": "Goldman Sachs", "series": "us daily fomc recap", "docType": "research", "filename": "us daily_ september fomc recap.pdf"}
    chosen = learn.select_examples(learn.examples(rows()), facts, "The FOMC raised the funds rate by 25bp; hawkish dots", k=2)
    assert chosen[0]["id"] == "u1"
    assert len(chosen) == 2


def test_a_series_with_three_rejections_and_no_approval_becomes_a_proposal_not_a_rule():
    proposals = {p["id"]: p for p in learn.proposals(rows())}
    fx = proposals["series_deny:ubs cio fx view"]
    assert fx["kind"] == "series_deny" and fx["downs"] == 4 and fx["ups"] == 0
    assert sorted(fx["evidence"]) == ["h1", "s0", "s1", "s2"]
    assert "series_deny:avolta" not in proposals, "one vote is not a pattern"
    assert not any(p["key"] == FOMC["series"] for p in proposals.values())


def test_an_approval_anywhere_in_the_series_blocks_the_proposal():
    extra = rows() + [vote("u9", "post", "up", {"title": "EURCHF", "publisher": "UBS", "series": "ubs cio fx view", "docType": "research"}, key="research-fx9")]
    assert "series_deny:ubs cio fx view" not in {p["id"] for p in learn.proposals(extra)}


def test_sync_writes_examples_upserts_proposals_and_loads_only_approved_rules(tmp_path):
    executed = []
    def execute(sql, args=()):
        executed.append((sql, args))
        if sql.startswith("SELECT"):
            return [("series_deny", "daily shortactivity us cleared"), ("doc_type_drop", "single_stock")]
        return []
    learn.sync(tmp_path, rows(), execute)
    assert len(json.loads((tmp_path / "examples.json").read_text())["examples"]) == 6
    inserts = [args for sql, args in executed if sql.startswith("INSERT")]
    assert [a[0] for a in inserts] == ["series_deny:ubs cio fx view"]
    assert all("ON CONFLICT(id) DO UPDATE SET downs" in sql and "status" not in sql.split("DO UPDATE SET")[1] for sql, _ in executed if sql.startswith("INSERT")), \
        "re-proposing never resets a decision the operator already made"
    assert learn.load_rules(tmp_path) == {"series_deny": {"daily shortactivity us cleared"}, "doc_type_drop": {"single_stock"}, "publisher_deny": set()}
    before = len(executed)
    learn.sync(tmp_path, rows(), execute)
    assert [sql for sql, _ in executed[before:] if sql.startswith("INSERT")] == [], "unchanged proposals are not rewritten every poll"


def test_approved_rules_drop_documents_with_their_own_reason_codes():
    ident = identify.Identity("UBS", "folder", "ubs", "ubs cio fx view", "2026-09-17", "text", 1, "17 September 2026", "research", "")
    rules = {"series_deny": {"ubs cio fx view"}, "doc_type_drop": set(), "publisher_deny": set()}
    assert triage.decide(ident, rules=rules) == ("drop", "SERIES_DENYLIST")
    assert triage.decide(ident, rules={"series_deny": set(), "doc_type_drop": {"research"}, "publisher_deny": set()}) == ("drop", "RULE_DOC_TYPE")
    assert triage.decide(ident, rules={"series_deny": set(), "doc_type_drop": set(), "publisher_deny": {"UBS"}}) == ("drop", "RULE_PUBLISHER")
    assert triage.decide(ident, rules=None) == ("review", None)


def test_intake_feeds_examples_to_the_selector_and_applies_approved_rules(tmp_path):
    publisher = SimpleNamespace(store_asset=lambda p: "/api/newsfeed/research/files/" + "b" * 64 + "." + str(p).rsplit(".", 1)[-1])
    (tmp_path / "examples.json").write_text(json.dumps({"examples": learn.examples(rows())}))
    reviewer = Reviewer([selection(), verdict()])
    pipe = intake.Pipeline(tmp_path, reviewer, publisher, extractor=extractor, figure_catalogue=catalogue, pdf_created=lambda pdf: None)
    assert len(pipe.process(work(), tmp_path / "r.pdf", [])) == 1
    prompt = reviewer.calls[0][1]
    assert "OPERATOR PREFERENCES" in prompt and "single stock, not for me" in prompt and "rates decisions always matter" in prompt

    (tmp_path / "rules.json").write_text(json.dumps({"series_deny": ["tic data"], "doc_type_drop": [], "publisher_deny": []}))
    blocked = dict(work(), key="z" * 64)
    assert pipe.process(blocked, tmp_path / "r.pdf", []) == []
    review = json.loads((tmp_path / "evidence" / ("z" * 64) / "review.json").read_text())
    assert review["outcome"] == "dropped" and review["reason_code"] == "SERIES_DENYLIST"
