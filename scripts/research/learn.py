"""Learn from operator votes.

Automatic: every vote becomes a worked example the selector sees (the most
similar examples per document, with the operator's own reasons and comments).
Never automatic: hard rules. A series, publisher or document type the
operator keeps rejecting becomes a PROPOSAL in Turso; only an approved
proposal changes triage, and an approval anywhere in the group blocks it.
"""
from __future__ import annotations
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from utils.atomic_io import atomic_save

EXAMPLES, RULES, PROPOSED = 'examples.json', 'rules.json', 'proposed.json'
MIN_REJECTIONS = {'series_deny': 3, 'publisher_deny': 6, 'doc_type_drop': 6}
_GROUP_FIELD = {'series_deny': 'series', 'publisher_deny': 'publisher', 'doc_type_drop': 'docType'}
_VERDICT = {('post', 'up'): 'wanted', ('post', 'down'): 'not wanted', ('held', 'up'): 'should have published', ('held', 'down'): 'correctly held'}


def _json(value, fallback):
    try:
        return json.loads(value) if value else fallback
    except (TypeError, ValueError):
        return fallback


def _latest(rows):
    latest = {}
    for row in sorted(rows, key=lambda r: r[9] or ''):
        latest[(row[1], row[2] or row[3])] = row
    return [row for row in latest.values() if row[5] in ('up', 'down')]


def examples(rows):
    out = []
    for id_, target, _post, _work, _file, vote, reasons, comment, snapshot, created in _latest(rows):
        snap = _json(snapshot, {})
        out.append({'id': id_, 'verdict': _VERDICT[(target, vote)], 'vote': vote, 'target': target,
                    'title': snap.get('title') or snap.get('fileName') or '', 'publisher': snap.get('publisher') or '',
                    'series': snap.get('series') or '', 'docType': snap.get('docType') or '', 'tags': snap.get('tags') or [],
                    'reasons': _json(reasons, []), 'comment': comment or '', 'at': created})
    return sorted(out, key=lambda e: e['at'] or '', reverse=True)


def _tokens(*parts):
    return set(re.findall(r'[a-z0-9]{3,}', ' '.join(str(p) for p in parts if p).lower()))


def select_examples(all_examples, facts, text, k=12):
    """Lexical similarity is enough at this label count; the newest vote breaks ties."""
    doc = _tokens(facts.get('publisher'), facts.get('series'), facts.get('docType'), facts.get('filename'), text[:1500])
    scored = []
    for index, example in enumerate(all_examples):
        tokens = _tokens(example['title'], example['publisher'], example['series'], example['docType'], ' '.join(example['tags']))
        bonus = 3 * (bool(example['series']) and example['series'] == facts.get('series')) + (bool(example['publisher']) and example['publisher'] == facts.get('publisher'))
        scored.append((len(doc & tokens) + bonus, -index, example))
    return [example for _score, _index, example in sorted(scored, key=lambda item: (item[0], item[1]), reverse=True)[:k]]


def proposals(rows):
    """Groups the operator keeps rejecting and has never approved."""
    out = []
    voted = examples(rows)
    for kind, field in _GROUP_FIELD.items():
        groups = defaultdict(lambda: {'downs': [], 'ups': 0})
        for example in voted:
            key = example[field]
            if not key or key == 'unknown':
                continue
            rejected = example['verdict'] == 'correctly held' or (example['verdict'] == 'not wanted' and 'not_relevant' in example['reasons'])
            if rejected:
                groups[key]['downs'].append(example['id'])
            elif example['vote'] == 'up':
                groups[key]['ups'] += 1
        for key, group in groups.items():
            if group['ups'] == 0 and len(group['downs']) >= MIN_REJECTIONS[kind]:
                out.append({'id': f'{kind}:{key}', 'kind': kind, 'key': key, 'downs': len(group['downs']), 'ups': 0, 'evidence': sorted(group['downs'])})
    return sorted(out, key=lambda p: p['id'])


def load_rules(root):
    try:
        stored = json.loads((Path(root) / RULES).read_text())
    except (OSError, ValueError):
        stored = {}
    return {kind: set(stored.get(kind) or []) for kind in _GROUP_FIELD}


def load_examples(root):
    try:
        return json.loads((Path(root) / EXAMPLES).read_text()).get('examples') or []
    except (OSError, ValueError, AttributeError):
        return []


def sync(root, rows, execute=None):
    """Refresh local examples and approved rules; upsert new or changed proposals. Never decides a proposal."""
    if execute is None:
        from api.db_http import hrana_execute as execute
    root = Path(root)
    atomic_save(str(root / EXAMPLES), {'examples': examples(rows)})
    try:
        known = json.loads((root / PROPOSED).read_text()).get('proposals', {})
    except (OSError, ValueError, AttributeError):
        known = {}
    now = datetime.now(timezone.utc).isoformat()
    current = {}
    for proposal in proposals(rows):
        current[proposal['id']] = proposal['downs']
        if known.get(proposal['id']) == proposal['downs']:
            continue
        execute("INSERT INTO research_rule_proposals (id,kind,key,downs,ups,evidence_json,status,created_at) VALUES (?,?,?,?,?,?,'proposed',?) "
                "ON CONFLICT(id) DO UPDATE SET downs=excluded.downs,ups=excluded.ups,evidence_json=excluded.evidence_json",
                (proposal['id'], proposal['kind'], proposal['key'], proposal['downs'], proposal['ups'], json.dumps(proposal['evidence']), now))
    atomic_save(str(root / PROPOSED), {'proposals': current})
    approved = defaultdict(list)
    for kind, key in execute("SELECT kind,key FROM research_rule_proposals WHERE status='approved'"):
        approved[kind].append(key)
    atomic_save(str(root / RULES), {kind: sorted(approved.get(kind, [])) for kind in _GROUP_FIELD})
