"""Operator votes: "should have published" and "want more" re-queue the document with the operator's note."""
import json

from research import feedback

KEY = "marketdesk:Aaaaaaaa1"


class Store:
    """The state protocol feedback.apply drives (research.marketdesk_store implements it in production)."""

    def __init__(self, keys=(KEY,), files=None, busy=()):
        self.keys = set(keys)
        self.files = dict(files or {})
        self.busy = set(busy)
        self.notes = {}
        self.handled = set()

    def feedback_handled(self):
        return set(self.handled)

    def work_key_for_file(self, file_id):
        return self.files.get(file_id)

    def requeue_with_note(self, key, note, *, feedback_id):
        if key not in self.keys or key in self.busy or key in self.notes:
            return False
        self.notes[key] = json.loads(note)
        self.handled.add(feedback_id)
        return True

    def has_live_work(self, key):
        return key in self.busy

    def mark_feedback_handled(self, feedback_id):
        self.handled.add(feedback_id)


def row(id_, target, vote, *, work_key=None, post_id=None, file_id="id:one", reasons=(), comment="", snapshot=None, created="2026-09-19T12:00:00Z"):
    return (id_, target, post_id, work_key, file_id, vote, json.dumps(list(reasons)), comment, json.dumps(snapshot or {}), created)


def test_actions_are_the_latest_actionable_vote_per_target():
    rows = [
        row("f1", "held", "up", work_key=KEY, comment="the range is on page 3"),
        row("f2", "held", "down", work_key="b" * 64),
        row("f3", "post", "up", post_id="research-" + "1" * 64, reasons=["want_more"], comment="add the dot plot", snapshot={"title": "FOMC hikes"}),
        row("f4", "post", "up", post_id="research-" + "2" * 64),                      # plain thumbs-up: nothing to do
        row("f5", "held", "up", work_key="c" * 64, created="2026-09-19T10:00:00Z"),
        row("f6", "held", "clear", work_key="c" * 64, created="2026-09-19T11:00:00Z"),  # withdrawn
    ]
    actions = feedback.actions(rows, handled={"f0"})
    assert [(a.id, a.kind) for a in actions] == [("f1", "publish"), ("f3", "more")]
    assert actions[0].work_key == KEY and actions[0].note == "the range is on page 3"
    assert actions[1].post_id == "research-" + "1" * 64 and actions[1].title == "FOMC hikes"
    assert feedback.actions(rows, handled={"f1", "f3"}) == []


def test_held_vote_requeues_the_document_with_the_operator_note_exactly_once():
    store = Store()
    fetch = lambda: [row("f1", "held", "up", work_key=KEY, comment="the range is on page 3")]
    assert feedback.apply(store, fetch) == 1
    assert store.notes[KEY] == {"kind": "publish", "comment": "the range is on page 3", "feedback_id": "f1"}
    assert feedback.apply(store, fetch) == 0, "a handled vote never re-queues again"


def test_want_more_finds_the_document_by_file_and_carries_the_post_to_revise():
    store = Store(files={"id:one": KEY})
    snapshot = {"title": "FOMC hikes 25 bps"}
    fetch = lambda: [row("f3", "post", "up", post_id="research-" + "1" * 64, reasons=["want_more"], comment="add the dot plot chart", snapshot=snapshot)]
    assert feedback.apply(store, fetch) == 1
    assert store.notes[KEY] == {"kind": "more", "comment": "add the dot plot chart", "feedback_id": "f3",
                                "post_id": "research-" + "1" * 64, "title": "FOMC hikes 25 bps"}


def test_vote_for_busy_work_stays_actionable_until_the_work_finishes():
    store = Store(busy={KEY})
    fetch = lambda: [row("f1", "held", "up", work_key=KEY, comment="apply after current review")]
    assert feedback.apply(store, fetch) == 0
    assert "f1" not in store.feedback_handled()
    store.busy.clear()
    assert feedback.apply(store, fetch) == 1
    assert "f1" in store.feedback_handled()


def test_vote_for_an_unknown_document_is_marked_handled_not_retried_forever():
    store = Store()
    assert feedback.apply(store, lambda: [row("f9", "held", "up", work_key="e" * 64)]) == 0
    assert "f9" in store.feedback_handled()


def test_a_fetch_failure_changes_nothing():
    store = Store()
    def boom():
        raise RuntimeError("turso down")
    assert feedback.apply(store, boom) == 0
    assert store.notes == {} and store.handled == set()


def test_examples_sync_failure_does_not_block_requeue(tmp_path, monkeypatch):
    from research import learn
    def broken(*_args, **_kwargs):
        raise OSError("disk full")
    monkeypatch.setattr(learn, "sync", broken)
    store = Store()
    assert feedback.apply(store, lambda: [row("f1", "held", "up", work_key=KEY)], root=tmp_path) == 1
