"""Act on operator votes: "should have published" and "want more" re-queue the document with the operator's note.

Votes live in Turso `research_feedback` (append-only, latest per target wins).
Handled vote ids are recorded in the local queue so an action runs exactly
once. A fetch failure changes nothing; a vote that names an unknown document
is marked handled so it is not retried forever.
"""
from __future__ import annotations
import json
from dataclasses import dataclass

LOOKBACK_DAYS = 14
_COLUMNS = 'id,target,post_id,work_key,file_id,vote,reasons,comment,snapshot_json,created_at'


@dataclass
class Action:
    id: str
    kind: str                # publish | more
    note: str
    work_key: str | None = None
    file_id: str | None = None
    post_id: str | None = None
    title: str = ''


def fetch_rows():
    from api.db_http import hrana_execute
    return hrana_execute(
        f"SELECT {_COLUMNS} FROM research_feedback WHERE created_at >= datetime('now', ?) ORDER BY created_at, rowid LIMIT 2000",
        (f'-{LOOKBACK_DAYS} days',))


def _json(value, fallback):
    try:
        return json.loads(value) if value else fallback
    except (TypeError, ValueError):
        return fallback


def actions(rows, handled=frozenset()):
    """Latest vote per target, kept only when it asks the worker to do something and has not been handled."""
    latest = {}
    for row in sorted(rows, key=lambda r: r[9] or ''):
        id_, target, post_id, work_key = row[0], row[1], row[2], row[3]
        latest[(target, post_id or work_key)] = row
    out = []
    for row in latest.values():
        id_, target, post_id, work_key, file_id, vote, reasons, comment, snapshot, _created = row
        if id_ in handled or vote != 'up':
            continue
        if target == 'held':
            out.append(Action(id_, 'publish', comment or '', work_key=work_key, file_id=file_id))
        elif 'want_more' in _json(reasons, []):
            out.append(Action(id_, 'more', comment or '', file_id=file_id, post_id=post_id, title=str(_json(snapshot, {}).get('title') or '')))
    return sorted(out, key=lambda a: a.id)


def apply(state, fetch=fetch_rows, root=None):
    """Re-queue every newly actionable vote. Returns how many documents were re-queued."""
    try:
        rows = fetch()
    except Exception:
        return 0
    if root is not None:
        try:
            from research import learn
            learn.sync(root, rows)
        except Exception:
            pass  # Examples and proposals are best effort; re-queue actions below must still run.
    requeued = 0
    for action in actions(rows, state.feedback_handled()):
        note = {'kind': action.kind, 'comment': action.note, 'feedback_id': action.id}
        if action.kind == 'more':
            note.update(post_id=action.post_id, title=action.title)
        key = action.work_key or state.work_key_for_file(action.file_id)
        if key and state.requeue_with_note(key, json.dumps(note)):
            requeued += 1
        state.mark_feedback_handled(action.id)
    return requeued
