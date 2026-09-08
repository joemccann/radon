"""Independent minute discovery, bounded extraction and durable review delivery."""
from __future__ import annotations
import json
import hashlib
import multiprocessing
import os
from pathlib import Path
import signal
import time
import threading

from research.dropbox import DropboxClient, DropboxError, content_hash
from utils.atomic_io import atomic_save
from datetime import datetime, timezone
from research.state import State


class Backoff:
    """One provider deadline shared by discovery and download processes."""
    def __init__(self, context):
        self.deadline = context.Value('d', 0)

    def remaining(self):
        with self.deadline.get_lock():
            return max(0, self.deadline.value - time.monotonic())

    def record(self, error):
        delay = getattr(error, 'retry_after', 0) or (60 if getattr(error, 'status', None) == 429 else 0)
        if delay:
            with self.deadline.get_lock():
                self.deadline.value = max(self.deadline.value, time.monotonic() + delay)


def stage_health(root, stage, state, error=None):
    atomic_save(str(Path(root) / f'health-{stage}.json'), {
        'stage': stage, 'state': state, 'updated_at': datetime.now(timezone.utc).isoformat(),
        'error': type(error).__name__ if error else None})


class GuardedClient:
    def __init__(self, client, backoff):
        self.client, self.backoff = client, backoff

    def list_page(self, *args, **kwargs):
        remaining = self.backoff.remaining()
        if remaining:
            raise DropboxError('Provider backoff active', status=429, retry_after=remaining)
        try:
            return self.client.list_page(*args, **kwargs)
        except Exception as error:
            self.backoff.record(error)
            raise


def extract_pdf(pdf, output):
    from research.pipeline import Pipeline
    return Pipeline.extract(None, pdf, output)


def evidence_files(output, evidence):
    names = ['evidence.json'] + [page['markdown_file'] for page in evidence['pages']]
    files = {}
    for name in names:
        if not isinstance(name, str) or Path(name).name != name:
            raise ValueError('Invalid evidence artifact name')
        path = Path(output) / name
        if path.is_symlink():
            raise ValueError('Symlink evidence artifact rejected')
        files[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return files


def seal_evidence(pdf, output):
    evidence = json.loads((Path(output) / 'evidence.json').read_text())
    source = hashlib.sha256(Path(pdf).read_bytes()).hexdigest()
    if evidence.get('source_sha256') != source:
        raise ValueError('Extracted evidence differs from source PDF')
    atomic_save(str(Path(output) / 'ingestion-manifest.json'),
                {'source_sha256': source, 'files': evidence_files(output, evidence)})


def cached_extract(pdf, output):
    """Verify cached source and every text artifact before evidence review."""
    output = Path(output)
    manifest = json.loads((output / 'ingestion-manifest.json').read_text())
    evidence = json.loads((output / 'evidence.json').read_text())
    source = hashlib.sha256(Path(pdf).read_bytes()).hexdigest()
    if (manifest.get('source_sha256') != source or evidence.get('source_sha256') != source
            or manifest.get('files') != evidence_files(output, evidence)):
        raise ValueError('Cached extraction integrity failed')
    return evidence


def parse_one(root, state, client, extractor, backoff):
    if backoff.remaining():
        return False
    rows = state.unparsed(limit=1)
    if not rows:
        return False
    work = rows[0]
    if not state.claim_parse(work['key']):
        return False
    try:
        stage_health(root, 'extraction', 'running')
        pdf = client.download(work['metadata'], Path(root) / 'downloads')
        output = Path(root) / 'evidence' / work['key']
        output.mkdir(parents=True, exist_ok=True, mode=0o700)
        extractor(pdf, output)
        seal_evidence(pdf, output)
        state.parsed(work['key'], str(pdf))
        stage_health(root, 'extraction', 'ok')
    except Exception as error:
        backoff.record(error)
        stage_health(root, 'extraction', 'error', error)
        state.parse_retry(work['key'], error, max(backoff.remaining(), min(3600, 60 * 2 ** work['parse_attempts'])))
    return True


def review_one(root, state, pipeline, publisher, publish):
    from research.worker import flush_outbox
    if publish:
        flush_outbox(state, publisher)
    rows = state.ready(limit=1)
    if not rows:
        return False
    work = rows[0]
    if not state.claim(work['key']):
        return False
    try:
        pdf = Path(work['pdf'])
        if pdf.is_symlink() or content_hash(pdf.read_bytes()) != work['metadata']['content_hash']:
            raise ValueError('Cached PDF content hash differs from queued revision')
        recent = publisher.recent_posts(days=90) + [r['payload'] for r in state.outbox(limit=1000)]
        posts = pipeline.process(work, Path(work['pdf']), recent,
                                 progress=lambda stage: stage_health(root, 'review', stage))
        state.complete(work['key'], {'status': 'reviewed', 'items': len(posts)}, publications=posts)
        stage_health(root, 'review', 'ok')
    except Exception as error:
        stage_health(root, 'review', 'error', error)
        if not state.is_processing(work['key']):
            return True
        if work['attempts'] >= 5:
            state.complete(work['key'], {'status': 'held', 'error': type(error).__name__})
        else:
            state.retry(work['key'], error, delay=min(3600, 60 * 2 ** work['attempts']))
    if publish:
        flush_outbox(state, publisher)
    return True


def _consumer(root, stop, wake, parsed, backoff, review, publish, client_factory=None, pipeline_factory=None, extractor=None):
    # Native PDF subprocesses belong to this group for bounded shutdown.
    os.setsid()
    from research.pipeline import Pipeline
    from research import publish as publisher
    from research.worker import heartbeat
    state = State(Path(root) / 'state.sqlite')
    client = pipeline = None
    try:
        while not stop.is_set():
            try:
                if review:
                    if pipeline is None:
                        from research.model import Reviewer
                        pipeline = pipeline_factory() if pipeline_factory else Pipeline(root, Reviewer(), publisher, extractor=cached_extract)
                    worked = review_one(root, state, pipeline, publisher, publish)
                    event = parsed
                else:
                    if backoff.remaining():
                        stop.wait(min(1, backoff.remaining()))
                        continue
                    if client is None:
                        client = client_factory() if client_factory else DropboxClient.from_env().connect()
                    worked = parse_one(root, state, client, extractor or extract_pdf, backoff)
                    if worked:
                        parsed.set()
                    event = wake
                if not worked:
                    event.wait(1)
                    event.clear()
            except Exception as error:
                backoff.record(error)
                stage_health(root, 'review' if review else 'extraction', 'error', error)
                stop.wait(max(1, min(60, backoff.remaining() or 5)))
    finally:
        state.close()


def run_daemon(root, state, interval, publish, stopping):
    from research.worker import discover, heartbeat
    context = multiprocessing.get_context('spawn')
    stop, wake, parsed = context.Event(), context.Event(), context.Event()
    backoff = Backoff(context)
    children = [context.Process(target=_consumer, args=(str(root), stop, wake, parsed, backoff, review, publish))
                for review in (False, True)]
    started = []
    reporter = None
    try:
        for child in children:
            child.start()
            started.append(child)
        reporter = threading.Thread(target=report_health, args=(root, stop), daemon=True)
        reporter.start()
        poll(root, state, interval, stopping, stop, wake, backoff,
             lambda: all(child.is_alive() for child in children))
    finally:
        stop.set()
        wake.set()
        parsed.set()
        stop_consumers(started)
        if reporter is not None:
            reporter.join(2)


def stop_consumers(children):
    until = time.monotonic() + 5
    for child in children:
        child.join(max(0, until - time.monotonic()))
    # A dead group leader may still own a live native parser descendant.
    for sig in (signal.SIGTERM, signal.SIGKILL):
        for child in children:
            try:
                os.killpg(child.pid, sig)
            except ProcessLookupError:
                if child.is_alive():
                    child.terminate() if sig == signal.SIGTERM else child.kill()
        until = time.monotonic() + 2
        for child in children:
            child.join(max(0, until - time.monotonic()))


def aggregate_health(root, health):
    health = dict(health)
    previous = health.get('last_error')
    if isinstance(previous, dict) and 'stages' in previous:
        health['last_error'] = previous.get('discovery')
        health['state'] = 'error' if health['last_error'] else 'ok'
    stages, errors = {}, []
    for stage in ('extraction', 'review'):
        path = Path(root) / f'health-{stage}.json'
        if path.exists():
            detail = json.loads(path.read_text())
            stages[stage] = detail
            if detail['state'] == 'error':
                errors.append(detail)
    health['stages'] = stages
    if errors:
        health['state'] = 'error'
        health['last_error'] = {'discovery': health['last_error'], 'stages': errors}
    return health


def report_health(root, stop):
    """Network telemetry cannot delay the independent local poll deadline."""
    from api.db_http import hrana_execute
    while not stop.is_set():
        try:
            health = json.loads((Path(root) / 'health.json').read_text())
            stamp = health['updated_at']
            health = aggregate_health(root, health)
            hrana_execute("""INSERT INTO service_health(service,state,last_attempt_finished_at,last_error,updated_at)
                VALUES(?,?,?,?,?) ON CONFLICT(service) DO UPDATE SET state=excluded.state,
                last_attempt_finished_at=excluded.last_attempt_finished_at,last_error=excluded.last_error,updated_at=excluded.updated_at""",
                ('dropbox-research',health['state'],stamp,json.dumps(health['last_error']) if health['last_error'] else None,stamp),
                timeout=2)
        except Exception:
            pass  # Local stage heartbeat remains authoritative during telemetry outages.
        stop.wait(60)


def poll(root, state, interval, stopping, stop, wake, backoff, healthy,
         client_factory=None, clock=None):
    from research.worker import discover, heartbeat
    clock = clock or time.monotonic
    client_factory = client_factory or (lambda: DropboxClient.from_env().connect())
    client, deadline = None, 0
    while not stopping():
        if not healthy():
            error = RuntimeError('Research consumer exited')
            heartbeat(root, 'error', error, stage='consumer', local_only=True)
            raise error
        now = clock()
        if now >= deadline and not backoff.remaining():
            deadline = now + interval
            try:
                if client is None:
                    client = GuardedClient(client_factory(), backoff)
                added = discover(client, state, current_only=True, on_page=wake.set)
                stage_health(root, 'discovery', 'ok')
                heartbeat(root, 'ok', stage='discovery', local_only=True)
                print(json.dumps({'stage': 'discovery', 'discovered': added, 'interval': interval}), flush=True)
            except Exception as error:
                backoff.record(error)
                stage_health(root, 'discovery', 'error', error)
                heartbeat(root, 'error', error, stage='discovery', local_only=True)
            finally:
                wake.set()
                health_path = Path(root) / 'health.json'
                if health_path.exists():
                    atomic_save(str(health_path), aggregate_health(root, json.loads(health_path.read_text())))
        stop.wait(1)
