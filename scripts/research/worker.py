"""Run the folder-scoped research queue and durable publication outbox."""
from __future__ import annotations
import argparse
import fcntl
import hashlib
import json
import os
import signal
import time
from datetime import datetime, timezone
from pathlib import Path
from research.dropbox import DropboxClient, DropboxError
from research.state import State, date_scopes
from utils.atomic_io import atomic_save


class DiscoveryError(DropboxError):
    """Safe summary of incomplete scopes after other scopes have been scanned."""
    def __init__(self, discovered, failures, retry_after=0):
        super().__init__('Dropbox discovery incomplete', retry_after=retry_after)
        self.discovered = discovered
        self.failures = failures


def heartbeat(root, state, error=None, stage=None, local_only=False):
    stamp = datetime.now(timezone.utc).isoformat()
    payload = {'service': 'dropbox-research', 'state': state, 'updated_at': stamp,
               'last_error': {'message': type(error).__name__} if error else None}
    if isinstance(error, DiscoveryError):
        payload['last_error']['discovery_errors'] = error.failures
    if stage:
        payload['stage'] = stage
    atomic_save(str(Path(root) / 'health.json'), payload)
    if local_only:
        return True
    from api.db_http import hrana_execute
    try:
        hrana_execute('''INSERT INTO service_health(service,state,last_attempt_finished_at,last_error,updated_at)
            VALUES(?,?,?,?,?) ON CONFLICT(service) DO UPDATE SET state=excluded.state,
            last_attempt_finished_at=excluded.last_attempt_finished_at,last_error=excluded.last_error,updated_at=excluded.updated_at''',
            ('dropbox-research',state,stamp,json.dumps(payload['last_error']) if error else None,stamp))
    except Exception:
        # Local heartbeat remains available if the reporting database is itself down.
        return False
    return True


def discover(client, state, now=None, current_only=False, on_page=None):
    dates = date_scopes(now)
    scopes = dict(dates[-1:] if current_only else dates)
    if not current_only:
        for scope in state.scopes():
            scopes.setdefault(scope, None)
    added = 0
    failures = []
    retry_after = 0
    for scope, folder_date in scopes.items():
        try:
            cursor = state.cursor(scope)
            reset = False
            for _ in range(100):
                try:
                    page = client.list_page(scope, cursor=cursor)
                except DropboxError as error:
                    if error.status == 409 and cursor and not reset:
                        state.reset_cursor(scope)
                        cursor, reset = None, True
                        continue
                    if error.status == 409 and not cursor:
                        # Date folder not yet created or previously watched folder removed.
                        break
                    raise
                added += state.ingest_page(scope, page, folder_date)
                if on_page is not None:
                    on_page()
                cursor = page['cursor']
                if not page.get('has_more'):
                    break
            else:
                raise RuntimeError('Dropbox pagination limit exceeded')
        except Exception as error:
            # Failed pages stay unacknowledged for retry; no revision is skipped.
            failure = {'stage': 'discovery', 'type': type(error).__name__,
                       'scope_id': hashlib.sha256(scope.encode()).hexdigest()[:16]}
            status = getattr(error, 'status', None)
            if isinstance(status, int):
                failure['status'] = status
            failures.append(failure)
            retry_after = max(retry_after, getattr(error, 'retry_after', 0) or 0)
            if status == 429 or retry_after:
                # Respect provider backoff before another listing or download.
                break
    if failures:
        raise DiscoveryError(added, failures, retry_after)
    return added


def flush_outbox(state, publisher):
    published = 0
    for row in state.outbox(limit=100):
        # Discovery may cancel a snapshot row; already-dispatched HTTP is the boundary.
        if not state.outbox_current(row['id'], row['work_key']):
            continue
        publisher.publish(row['payload'])
        state.published(row['id'])
        published += 1
    return published


def cycle(root, client, state, pipeline, publisher, publish=False, limit=4):
    published = flush_outbox(state, publisher) if publish else 0
    discovery_errors = []
    retry_after = 0
    try:
        added = discover(client, state)
    except DiscoveryError as error:
        added = error.discovered
        discovery_errors = error.failures
        retry_after = error.retry_after
    # Include pending outbox posts so dry-run and resumed cycles suppress duplicates too.
    recent = publisher.recent_posts(days=90) + [r['payload'] for r in state.outbox(limit=1000)]
    processed = 0
    errors = [failure['type'] for failure in discovery_errors]
    throttled = retry_after or any(failure.get('status') == 429 for failure in discovery_errors)
    for work in ([] if throttled else state.pending(limit=limit)):
        if not state.claim(work['key']):
            continue
        try:
            pdf = client.download(work['metadata'], Path(root) / 'downloads')
            posts = pipeline.process(
                work, pdf, recent,
                progress=lambda stage: heartbeat(root, 'running', stage=stage),
            )
            state.complete(work['key'], {'status': 'reviewed', 'items': len(posts)}, publications=posts)
            recent.extend(posts)
            processed += 1
        except Exception as error:
            errors.append(type(error).__name__)
            if work['attempts'] >= 5:
                from research.model import safe_error_message
                state.complete(work['key'], {'status': 'held', 'error': safe_error_message(error)})
            else:
                delay = max(getattr(error, 'retry_after', 0) or 0, min(3600, 60 * 2 ** work['attempts']))
                state.retry(work['key'], error, delay=delay)
    published += flush_outbox(state, publisher) if publish else 0
    result = {'discovered': added, 'processed': processed, 'published': published, 'errors': errors}
    if discovery_errors:
        result['discovery_errors'] = discovery_errors
    if retry_after:
        result['retry_after'] = retry_after
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--daemon', action='store_true')
    parser.add_argument('--publish', action='store_true', default=os.environ.get('RADON_RESEARCH_PUBLISH') == '1')
    parser.add_argument('--root', default=os.environ.get('RADON_RESEARCH_DIR', '/var/lib/radon/research'))
    parser.add_argument('--interval', type=int, default=60)
    parser.add_argument('--seed-reviewed', type=Path)
    args = parser.parse_args()
    if args.interval < 30:
        parser.error('interval must be at least30seconds')
    os.umask(0o077)
    root = Path(args.root)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.environ['RADON_RESEARCH_DIR'] = str(root)
    lock = (root / 'worker.lock').open('a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit('Research worker already running') from None
    state = State(root / 'state.sqlite')
    state.recover()
    from research import publish as publisher
    if args.seed_reviewed:
        from research.seed import seed_reviewed
        count = seed_reviewed(args.seed_reviewed, state, publisher)
        published = flush_outbox(state, publisher) if args.publish else 0
        print(json.dumps({'seeded': count, 'published': published}))
        return
    stopping = False
    def stop(_signal, _frame):
        nonlocal stopping
        stopping = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    if args.daemon:
        from research.ingestion import run_daemon
        try:
            run_daemon(root, state, min(args.interval, 60), args.publish, lambda: stopping)
        finally:
            state.close()
        return
    from research.model import Reviewer
    from research.pipeline import Pipeline
    pipeline = Pipeline(root, Reviewer(), publisher)
    client = DropboxClient.from_env().connect()
    exit_status = 0
    while not stopping:
        delay = args.interval
        try:
            heartbeat(root, 'running')
            result = cycle(root, client, state, pipeline, publisher, args.publish)
            exit_status = 1 if result['errors'] else 0
            error = RuntimeError() if result['errors'] else None
            if result.get('discovery_errors'):
                error = DiscoveryError(result['discovered'], result['discovery_errors'], result.get('retry_after', 0))
            heartbeat(root, 'error' if error else 'ok', error)
            delay = max(delay, result.get('retry_after', 0))
            print(json.dumps(result), flush=True)
        except Exception as error:
            heartbeat(root, 'error', error)
            print(json.dumps({'error': type(error).__name__}), flush=True)
            delay = max(delay, getattr(error, 'retry_after', 0) or 0)
            if not args.daemon:
                raise SystemExit(1) from None
        if not args.daemon:
            break
        deadline = time.monotonic() + delay
        while not stopping and time.monotonic() < deadline:
            time.sleep(min(1, max(0, deadline - time.monotonic())))
    state.close()
    if not args.daemon and exit_status:
        raise SystemExit(exit_status)


if __name__ == '__main__':
    main()
