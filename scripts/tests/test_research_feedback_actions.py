"""The worker acts on operator votes: "should have published" and "want more" re-queue the document with the operator's note."""
import json
import sqlite3
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


@pytest.mark.parametrize('busy_status', ['pending', 'processing'])
def test_held_vote_waits_for_busy_document_then_applies_exact_note_once(state, busy_status):
    s, key = state
    current_note = json.dumps({'kind': 'publish', 'comment': 'Current review instruction', 'feedback_id': 'prior'})
    assert s.requeue_with_note(key, current_note)
    if busy_status == 'processing':
        assert s.claim(key)
    with s.db:
        s.db.execute('UPDATE work SET attempts=4,error=? WHERE key=?', ('previous_retry', key))
    before = dict(s.db.execute('SELECT * FROM work WHERE key=?', (key,)).fetchone())
    fetch = lambda: [row('arrived-while-busy', 'held', 'up', work_key=key,
                         comment='Use the exact source table after this review finishes')]
    assert feedback.apply(s, fetch) == 0
    assert 'arrived-while-busy' not in s.feedback_handled(), 'busy work must not consume an unapplied vote'
    assert dict(s.db.execute('SELECT * FROM work WHERE key=?', (key,)).fetchone()) == before
    if busy_status == 'pending':
        assert s.claim(key)
    s.complete(key, {'status': 'reviewed', 'items': 0})
    assert feedback.apply(s, fetch) == 1
    pending = s.pending()[0]
    assert pending['key'] == key and pending['attempts'] == 0
    assert json.loads(pending['note']) == {
        'kind': 'publish', 'comment': 'Use the exact source table after this review finishes',
        'feedback_id': 'arrived-while-busy',
    }
    assert 'arrived-while-busy' in s.feedback_handled()
    after = dict(s.db.execute('SELECT * FROM work WHERE key=?', (key,)).fetchone())
    assert feedback.apply(s, fetch) == 0
    assert dict(s.db.execute('SELECT * FROM work WHERE key=?', (key,)).fetchone()) == after


@pytest.mark.parametrize('older_status', ['complete', 'published'])
@pytest.mark.parametrize('busy_status', ['pending', 'processing'])
def test_file_vote_waits_for_latest_busy_revision_instead_of_requeueing_older_work(state, older_status, busy_status):
    s, older_key = state
    s.ingest_page('2026/September/Sep 07', {'cursor': 'new-revision', 'entries': [entry(rev='rev2')]}, '2026-09-07')
    latest_key = s.pending()[0]['key']
    # Published history is retained; complete is also possible in legacy queue snapshots.
    with s.db:
        s.db.execute('UPDATE work SET status=? WHERE key=?', (older_status, older_key))
        s.db.execute('UPDATE work SET note=?,attempts=3 WHERE key=?',
                     (json.dumps({'kind': 'publish', 'comment': 'Current revision note', 'feedback_id': 'current'}), latest_key))
    if busy_status == 'processing':
        assert s.claim(latest_key)
    older_before = dict(s.db.execute('SELECT * FROM work WHERE key=?', (older_key,)).fetchone())
    latest_before = dict(s.db.execute('SELECT * FROM work WHERE key=?', (latest_key,)).fetchone())
    post_id = 'research-' + '1' * 64
    fetch = lambda: [row('latest-revision-vote', 'post', 'up', post_id=post_id, reasons=['want_more'],
                         comment='Add the measured chart after the current revision finishes',
                         snapshot={'title': 'Existing finding'})]
    assert feedback.apply(s, fetch) == 0, 'an older completed revision must not jump ahead of current work'
    assert s.work_key_for_file('id:one') == latest_key
    assert 'latest-revision-vote' not in s.feedback_handled()
    assert dict(s.db.execute('SELECT * FROM work WHERE key=?', (older_key,)).fetchone()) == older_before
    assert dict(s.db.execute('SELECT * FROM work WHERE key=?', (latest_key,)).fetchone()) == latest_before
    if busy_status == 'pending':
        assert s.claim(latest_key)
    s.complete(latest_key, {'status': 'reviewed', 'items': 0})
    assert feedback.apply(s, fetch) == 1
    pending = s.pending()[0]
    assert pending['key'] == latest_key and pending['attempts'] == 0
    assert json.loads(pending['note']) == {
        'kind': 'more', 'comment': 'Add the measured chart after the current revision finishes',
        'feedback_id': 'latest-revision-vote', 'post_id': post_id, 'title': 'Existing finding',
    }
    assert 'latest-revision-vote' in s.feedback_handled()
    assert feedback.apply(s, fetch) == 0
    assert dict(s.db.execute('SELECT * FROM work WHERE key=?', (older_key,)).fetchone()) == older_before


def test_vote_is_not_lost_when_review_finishes_after_requeue_refusal(state, monkeypatch):
    s, key = state
    assert s.requeue_with_note(key, json.dumps({'comment': 'Current work'}))
    assert s.claim(key)
    original_requeue = s.requeue_with_note
    calls = []
    def finishes_during_refusal(work_key, note, **kwargs):
        result = original_requeue(work_key, note, **kwargs)
        calls.append(result)
        if len(calls) == 1:
            assert result is False
            s.complete(key, {'status': 'reviewed', 'items': 0})
        return result
    monkeypatch.setattr(s, 'requeue_with_note', finishes_during_refusal)
    fetch = lambda: [row('finish-race', 'held', 'up', work_key=key, comment='Apply after current review')]
    assert feedback.apply(s, fetch) == 0
    assert 'finish-race' not in s.feedback_handled()
    assert s.db.execute('SELECT status FROM work WHERE key=?', (key,)).fetchone()[0] == 'complete'
    assert feedback.apply(s, fetch) == 1
    assert json.loads(s.pending()[0]['note']) == {
        'kind': 'publish', 'comment': 'Apply after current review', 'feedback_id': 'finish-race',
    }
    assert 'finish-race' in s.feedback_handled()
    assert feedback.apply(s, fetch) == 0


def test_feedback_note_and_handled_marker_commit_atomically(state):
    s, key = state
    with s.db:
        s.db.execute('UPDATE work SET attempts=4,note=? WHERE key=?', ('previous context', key))
        s.db.execute("""CREATE TRIGGER reject_feedback_marker BEFORE INSERT ON feedback_handled
            BEGIN SELECT RAISE(ABORT, 'feedback marker unavailable'); END""")
    before = dict(s.db.execute('SELECT * FROM work WHERE key=?', (key,)).fetchone())
    fetch = lambda: [row('atomic-vote', 'held', 'up', work_key=key, comment='Do not lose this instruction')]
    with pytest.raises(sqlite3.IntegrityError, match='feedback marker unavailable'):
        feedback.apply(s, fetch)
    assert dict(s.db.execute('SELECT * FROM work WHERE key=?', (key,)).fetchone()) == before
    assert 'atomic-vote' not in s.feedback_handled()
    with s.db:
        s.db.execute('DROP TRIGGER reject_feedback_marker')
    assert feedback.apply(s, fetch) == 1
    assert json.loads(s.pending()[0]['note']) == {
        'kind': 'publish', 'comment': 'Do not lose this instruction', 'feedback_id': 'atomic-vote',
    }
    assert 'atomic-vote' in s.feedback_handled()
    assert feedback.apply(s, fetch) == 0


def test_stale_handled_snapshot_cannot_apply_the_same_vote_again(state, monkeypatch):
    s, key = state
    captured_handled = s.feedback_handled()
    monkeypatch.setattr(s, 'feedback_handled', lambda: captured_handled)
    fetch = lambda: [row('stale-snapshot-vote', 'held', 'up', work_key=key, comment='Apply exactly once')]
    assert feedback.apply(s, fetch) == 1
    assert s.claim(key)
    s.complete(key, {'status': 'reviewed', 'items': 0})
    completed = dict(s.db.execute('SELECT * FROM work WHERE key=?', (key,)).fetchone())
    assert feedback.apply(s, fetch) == 0, 'the transaction must reject an already handled vote despite stale reads'
    assert dict(s.db.execute('SELECT * FROM work WHERE key=?', (key,)).fetchone()) == completed
    assert s.db.execute('SELECT COUNT(*) FROM feedback_handled WHERE id=?', ('stale-snapshot-vote',)).fetchone()[0] == 1


@pytest.mark.parametrize('terminal_status', ['deleted', 'superseded'])
def test_vote_for_terminal_target_is_still_marked_handled_without_changing_work(state, terminal_status):
    s, key = state
    with s.db:
        s.db.execute('UPDATE work SET status=? WHERE key=?', (terminal_status, key))
    before = dict(s.db.execute('SELECT * FROM work WHERE key=?', (key,)).fetchone())
    fetch = lambda: [row('terminal-vote', 'held', 'up', work_key=key, comment='Stale target')]
    assert feedback.apply(s, fetch) == 0
    assert 'terminal-vote' in s.feedback_handled()
    assert dict(s.db.execute('SELECT * FROM work WHERE key=?', (key,)).fetchone()) == before


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
    verify_prompt = reviewer.calls[-1][1]
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
