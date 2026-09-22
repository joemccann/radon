"""The worker acts on operator votes: "should have published" and "want more" re-queue the document with the operator's note."""
import json
from types import SimpleNamespace

import pytest

from research import feedback, intake
from research.state import State
from tests.test_research_intake import PAGE1, Reviewer, catalogue, extractor, selection, verdict, work


def entry(file_id="id:one", rev="rev1"):
    return {".tag": "file", "id": file_id, "rev": rev, "content_hash": "a" * 64, "name": "report.pdf",
            "path_lower": "/joe mccann/current/2026/september/sep 07/report.pdf",
            "path_display": "/Joe McCann/Current/2026/September/Sep 07/report.pdf"}


@pytest.fixture
def state(tmp_path):
    root = tmp_path / "private"; root.mkdir(mode=0o700)
    s = State(root / "state.sqlite")
    s.ingest_page("2026/September/Sep 07", {"cursor": "c", "entries": [entry()]}, "2026-09-07")
    key = s.pending()[0]["key"]
    s.claim(key); s.complete(key, {"status": "reviewed", "items": 0})
    yield s, key
    s.close()


def row(id_, target, vote, *, work_key=None, post_id=None, file_id="id:one", reasons=(), comment="", snapshot=None, created="2026-09-19T12:00:00Z"):
    return (id_, target, post_id, work_key, file_id, vote, json.dumps(list(reasons)), comment, json.dumps(snapshot or {}), created)


def test_actions_are_the_latest_actionable_vote_per_target(state):
    _, key = state
    rows = [
        row("f1", "held", "up", work_key=key, comment="the range is on page 3"),
        row("f2", "held", "down", work_key="b" * 64),
        row("f3", "post", "up", post_id="research-" + "1" * 64, reasons=["want_more"], comment="add the dot plot", snapshot={"title": "FOMC hikes"}),
        row("f4", "post", "up", post_id="research-" + "2" * 64),                      # plain thumbs-up: nothing to do
        row("f5", "held", "up", work_key="c" * 64, created="2026-09-19T10:00:00Z"),
        row("f6", "held", "clear", work_key="c" * 64, created="2026-09-19T11:00:00Z"),  # withdrawn
    ]
    actions = feedback.actions(rows, handled={"f0"})
    assert [(a.id, a.kind) for a in actions] == [("f1", "publish"), ("f3", "more")]
    assert actions[0].work_key == key and actions[0].note == "the range is on page 3"
    assert actions[1].post_id == "research-" + "1" * 64 and actions[1].title == "FOMC hikes"
    assert feedback.actions(rows, handled={"f1", "f3"}) == []


def test_held_vote_requeues_the_document_with_the_operator_note_exactly_once(state):
    s, key = state
    fetch = lambda: [row("f1", "held", "up", work_key=key, comment="the range is on page 3")]
    assert feedback.apply(s, fetch) == 1
    work = s.pending()[0]
    assert work["key"] == key and work["attempts"] == 0
    assert json.loads(work["note"]) == {"kind": "publish", "comment": "the range is on page 3", "feedback_id": "f1"}
    assert feedback.apply(s, fetch) == 0, "a handled vote never re-queues again"


def test_want_more_finds_the_document_by_file_and_revision_and_carries_the_post_to_revise(state):
    s, key = state
    snapshot = {"title": "FOMC hikes 25 bps"}
    fetch = lambda: [row("f3", "post", "up", post_id="research-" + "1" * 64, reasons=["want_more"], comment="add the dot plot chart", snapshot=snapshot)]
    assert feedback.apply(s, fetch) == 1
    note = json.loads(s.pending()[0]["note"])
    assert note == {"kind": "more", "comment": "add the dot plot chart", "feedback_id": "f3", "post_id": "research-" + "1" * 64, "title": "FOMC hikes 25 bps"}


def test_vote_for_an_unknown_document_is_marked_handled_not_retried_forever(state):
    s, _ = state
    fetch = lambda: [row("f9", "held", "up", work_key="e" * 64)]
    assert feedback.apply(s, fetch) == 0
    assert "f9" in s.feedback_handled()


def test_a_fetch_failure_changes_nothing(state):
    s, _ = state
    def boom():
        raise RuntimeError("turso down")
    assert feedback.apply(s, boom) == 0
    assert s.pending() == []


# --- intake honours the note ------------------------------------------------

@pytest.fixture
def publisher():
    return SimpleNamespace(store_asset=lambda p: "/api/newsfeed/research/files/" + "b" * 64 + "." + str(p).rsplit(".", 1)[-1])


def build(tmp_path, reviewer, publisher):
    return intake.Pipeline(tmp_path, reviewer, publisher, extractor=extractor, figure_catalogue=catalogue, pdf_created=lambda pdf: None)


def test_operator_note_reaches_the_selector_and_overrides_a_triage_drop(tmp_path, publisher):
    reviewer = Reviewer([selection(), verdict()])
    noted = dict(work("gbpusd_en_1666701.pdf"), note=json.dumps({"kind": "publish", "comment": "the positioning table matters", "feedback_id": "f1"}))
    posts = build(tmp_path, reviewer, publisher).process(noted, tmp_path / "r.pdf", [])
    assert len(posts) == 1, "an operator should-have-published vote bypasses the document-type drop"
    assert "OPERATOR NOTE" in reviewer.calls[0][1] and "the positioning table matters" in reviewer.calls[0][1]


def test_want_more_revises_the_same_post_and_is_not_judged_a_duplicate_of_itself(tmp_path, publisher):
    post_id = "research-" + "1" * 64
    reviewer = Reviewer([selection(), verdict()])
    noted = dict(work(), note=json.dumps({"kind": "more", "comment": "add the chart", "feedback_id": "f3", "post_id": post_id, "title": "Foreign investors bought $45bn"}))
    recent = [{"id": post_id, "title": "Foreign investors bought $45bn of US equities in July", "content": PAGE1, "timestamp": "2026-09-19T08:00:00Z"},
              {"id": "research-other", "title": "Other", "content": "x", "timestamp": "2026-09-19T07:00:00Z"}]
    posts = build(tmp_path, reviewer, publisher).process(noted, tmp_path / "r.pdf", recent)
    assert [p["id"] for p in posts] == [post_id], "the revision updates the existing post in place"
    assert "REVISE THIS PUBLISHED ITEM" in reviewer.calls[0][1]
    verify_prompt = reviewer.calls[1][1]
    assert post_id not in verify_prompt and "research-other" in verify_prompt


def test_state_accepts_a_revised_payload_for_the_same_document(state):
    s, key = state
    s.db.execute("UPDATE work SET status='pending' WHERE key=?", (key,)); s.db.commit()
    s.claim(key); s.complete(key, {"status": "reviewed", "items": 1}, publications=[{"id": "research-" + "1" * 64, "title": "v1"}])
    s.published("research-" + "1" * 64)
    s.db.execute("UPDATE work SET status='pending' WHERE key=?", (key,)); s.db.commit()
    s.claim(key); s.complete(key, {"status": "reviewed", "items": 1}, publications=[{"id": "research-" + "1" * 64, "title": "v2 with chart"}])
    pending = s.outbox()
    assert [p["payload"]["title"] for p in pending] == ["v2 with chart"]


def test_an_operator_requeue_jumps_the_backlog(state, tmp_path):
    s, key = state
    s.ingest_page("2026/September/Sep 07", {"cursor": "c2", "entries": [entry("id:two", "rev9") | {"path_lower": "/joe mccann/current/2026/september/sep 07/newer.pdf", "path_display": "/Joe McCann/Current/2026/September/Sep 07/newer.pdf"}]}, "2026-09-07")
    feedback.apply(s, lambda: [row_("f1", key)])
    for found in s.db.execute("SELECT key FROM work").fetchall():
        s.claim_parse(found["key"]); s.parsed(found["key"], str(tmp_path / "x.pdf"))
    assert [w["key"] for w in s.ready(limit=2)][0] == key, "the newer document would otherwise be reviewed first"


def row_(id_, key):
    return row(id_, "held", "up", work_key=key, comment="please")
