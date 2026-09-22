"""Offline replay harness over cached research evidence.

Reads the private queue and evidence directory read-only, joins the golden set
(published posts + operator scope labels) and scores any per-document stage
function without network or model calls. Every v2 stage is measured here
before it runs in production.
"""
from __future__ import annotations
import argparse
import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

LIVE_STATUSES = ('pending', 'processing', 'complete', 'published')
QUOTA_MARKERS = ('ladder exhausted', 'quota', 'rate limit')


@dataclass
class Document:
    key: str
    name: str
    path: str
    folder_date: str
    status: str
    result: dict
    metadata: dict
    evidence_dir: Path

    @property
    def publisher_folder(self):
        parts = self.path.split('/')
        # /joe mccann/current/<year>/<month>/<day>/<publisher>/.../<file>
        return parts[6] if len(parts) > 7 else ''

    @property
    def page_count(self):
        try:
            return int(json.loads((self.evidence_dir / 'evidence.json').read_text())['page_count'])
        except (OSError, ValueError, KeyError):
            return 0

    def page_text(self, number):
        try:
            return (self.evidence_dir / f'page-{number:04d}.md').read_text()
        except OSError:
            return ''

    def review(self):
        try:
            return json.loads((self.evidence_dir / 'review.json').read_text())
        except (OSError, ValueError):
            return None


class Corpus:
    def __init__(self, root):
        self.root = Path(root)

    def _db(self):
        return sqlite3.connect(f'file:{self.root / "state.sqlite"}?mode=ro', uri=True)

    def documents(self):
        db = self._db()
        try:
            rows = db.execute('SELECT key,path,folder_date,status,result,metadata FROM work ORDER BY rowid').fetchall()
        finally:
            db.close()
        for key, path, folder_date, status, result, metadata in rows:
            if status not in LIVE_STATUSES:
                continue
            try:
                parsed = json.loads(result) if result else {}
            except ValueError:
                parsed = {}
            yield Document(key, path.split('/')[-1], path, folder_date or '', status, parsed,
                           json.loads(metadata), self.root / 'evidence' / key)


@dataclass
class Golden:
    positives: dict            # work key -> published post titles
    labels: dict               # full work key -> in | out | unsure

    @classmethod
    def load(cls, root, labels_path=None):
        db = sqlite3.connect(f'file:{Path(root) / "state.sqlite"}?mode=ro', uri=True)
        try:
            rows = db.execute("SELECT work_key,payload FROM outbox WHERE status='published'").fetchall()
            keys = [r[0] for r in db.execute('SELECT key FROM work')]
        finally:
            db.close()
        positives = {}
        for key, payload in rows:
            positives.setdefault(key, []).append(json.loads(payload).get('title', ''))
        labels = {}
        if labels_path:
            # Operator labels carry the 16-character key prefix shown on the labelling page.
            prefixes = json.loads(Path(labels_path).read_text()).get('labels', {})
            labels = {k: prefixes[k[:16]] for k in keys if k[:16] in prefixes}
        return cls(positives, labels)

    def label(self, key):
        return self.labels.get(key)

    def keys_labelled(self, value):
        return {k for k, v in self.labels.items() if v == value}

    @property
    def in_scope(self):
        return set(self.positives) | self.keys_labelled('in')


@dataclass
class Outcome:
    """What a stage function says about one document."""
    posts: list = field(default_factory=list)
    calls: int = 0
    dropped: str | None = None


def _finish(report, golden, produced, dropped_keys):
    positives = set(golden.positives)
    hit = {k for k in positives if produced.get(k)}
    report['positive_recall'] = (len(hit) / len(positives)) if positives else None
    report['lost_positives'] = sorted(positives - hit)
    in_scope, out_scope = golden.in_scope, golden.keys_labelled('out')
    report['in_scope_dropped'] = len(in_scope & dropped_keys)
    report['out_scope_dropped'] = len(out_scope & dropped_keys)
    return report


def baseline_v1(corpus, golden):
    """Score the production pipeline from its own review.json audits."""
    report = {'mode': 'v1-baseline', 'docs': 0, 'model_calls': 0, 'candidates': 0,
              'docs_with_posts': 0, 'quota_holds': 0, 'reached_model': 0}
    produced = {}
    for doc in corpus.documents():
        report['docs'] += 1
        error = (doc.result.get('error') or '').lower()
        if doc.result.get('status') == 'held' and any(m in error for m in QUOTA_MARKERS):
            report['quota_holds'] += 1
        review = doc.review()
        if not review:
            continue
        report['reached_model'] += 1
        for entry in review.get('audit', []):
            report['model_calls'] += sum(k in entry for k in ('selection', 'inspection', 'crop_correction', 'verification'))
            if 'selection' in entry:
                report['candidates'] += len(entry['selection'].get('candidates') or [])
        if review.get('posts'):
            report['docs_with_posts'] += 1
            produced[doc.key] = review['posts']
    in_scope = golden.in_scope
    reached = {d.key for d in corpus.documents() if d.review()}
    report['in_scope_reached_model'] = (len(in_scope & reached) / len(in_scope)) if in_scope else None
    return _finish(report, golden, produced, set())


def evaluate(run, corpus, golden, keys=None):
    """Score a stage function: run(Document) -> Outcome."""
    report = {'mode': 'evaluate', 'docs': 0, 'model_calls': 0, 'docs_with_posts': 0, 'dropped': {}}
    produced, dropped_keys = {}, set()
    for doc in corpus.documents():
        if keys and doc.key not in keys:
            continue
        report['docs'] += 1
        outcome = run(doc)
        report['model_calls'] += outcome.calls
        if outcome.dropped:
            report['dropped'][outcome.dropped] = report['dropped'].get(outcome.dropped, 0) + 1
            dropped_keys.add(doc.key)
            continue
        if outcome.posts:
            report['docs_with_posts'] += 1
            produced[doc.key] = outcome.posts
    report['calls_per_doc'] = (report['model_calls'] / report['docs']) if report['docs'] else 0.0
    return _finish(report, golden, produced, dropped_keys)


def mirror_outcomes(corpus, record=None, store=None):
    """Backfill the Turso outcome mirror for every document that already has a v2 audit."""
    if record is None:
        from research.publish import record_outcome as record, store_asset as store
    count = 0
    for doc in corpus.documents():
        review = doc.review()
        if not review or review.get('pipeline') != 'v2':
            continue
        if 'document' not in review:
            # Audits written before the context column: rebuild it from the cached extraction.
            document = {'page_count': doc.page_count, 'excerpt': ' '.join(doc.page_text(1).split())[:900]}
            if not review.get('posts') and store is not None:
                try:
                    document['source_url'] = store(json.loads((doc.evidence_dir / 'evidence.json').read_text())['source_path'])
                except Exception:
                    pass
            review['document'] = document
        record({'key': doc.key, 'folder_date': doc.folder_date, 'metadata': doc.metadata}, review)
        count += 1
    return count


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, help='private research directory (state.sqlite + evidence/)')
    parser.add_argument('--labels', help='operator scope labels JSON')
    parser.add_argument('--baseline', action='store_true', help='score the v1 production audits')
    parser.add_argument('--mirror-outcomes', action='store_true', help='backfill research_outcomes in Turso from v2 review.json audits')
    args = parser.parse_args(argv)
    corpus = Corpus(args.root)
    golden = Golden.load(args.root, args.labels)
    if args.baseline:
        print(json.dumps(baseline_v1(corpus, golden), indent=1))
        return
    if args.mirror_outcomes:
        print(json.dumps({'mirrored': mirror_outcomes(corpus)}))
        return
    parser.error('choose --baseline or --mirror-outcomes')


if __name__ == '__main__':
    main()
