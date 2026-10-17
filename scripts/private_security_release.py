#!/usr/bin/env python3
"""Validate held P1 changes privately before the operator publishes a public PR.

The bot exports immutable Git objects using its existing private-report deploy
key. Only the separately installed operator controller can publish publicly.
P0 findings never enter this path. No model or bot marker grants authority.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

PUBLIC = 'joemccann/radon'
PRIVATE = 'joemccann/radon-security-reports'
SHA = re.compile('[0-9a-f]{40}')
WORKFLOW_PATHS = tuple('.github/workflows/' + name for name in ('ci.yml', 'pr-body.yml', 'app-images-pr.yml', 'app-images.yml'))


MAX_FEEDBACK_BYTES = 131072
FEEDBACK_STATUSES = frozenset(('private-checks-failed', 'private-review-rejected', 'private-review-invalid',
    'public-checks-invalid', 'public-checks-failed', 'public-head-changed', 'public-pr-closed',
    'public-diff-invalid', 'public-review-rejected', 'public-review-invalid', 'released'))


def _redact_feedback(value, limit=2048):
    text = str(value)
    text = re.sub(r'-----BEGIN [^-]*PRIVATE KEY-----[\s\S]*', '[REDACTED PRIVATE KEY]', text)
    text = re.sub(r'(?i)\bBearer\s+[^\s`"\']+', 'Bearer [REDACTED]', text)
    text = re.sub(r'\b(?:gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+|sk-[A-Za-z0-9_-]{12,}|(?:AKIA|ASIA)[A-Z0-9]{16}|eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)', '[REDACTED]', text)
    text = re.sub(r'''(?i)\b((?:[a-z_]*(?:token|password|secret|api[_-]?key|access[_-]?key|private[_-]?key)|PUSHOVER_USER))\s*[:=]\s*(?:"[^"\n]*"|'[^'\n]*'|[^\s,;]+)''', r'\1=[REDACTED]', text)
    text = re.sub(r'(https?://)[^/\s:@]+:[^/\s@]+@', r'\1[REDACTED]@', text)
    return text.encode()[:limit].decode(errors='ignore')


def _feedback_ref(base, head):
    if not isinstance(base, str) or not isinstance(head, str) or not SHA.fullmatch(base) or not SHA.fullmatch(head):
        raise ValueError('invalid immutable feedback identity')
    return f'refs/heads/validation/feedback/{base}/{head}'


def _validate_feedback(value, base, head):
    _feedback_ref(base, head)
    fields = {'schema', 'base_sha', 'head_sha', 'status', 'checks', 'findings', 'review_input_digests', 'release'}
    if (not isinstance(value, dict) or set(value) != fields or type(value['schema']) is not int or value['schema'] != 1
            or value['base_sha'] != base or value['head_sha'] != head or value['status'] not in FEEDBACK_STATUSES):
        raise ValueError('invalid feedback schema or identity')
    for name, keys, maximum in (('checks', {'name', 'status', 'conclusion'}, 100), ('findings', {'reviewer', 'message'}, 40)):
        rows = value[name]
        if not isinstance(rows, list) or len(rows) > maximum:
            raise ValueError('invalid feedback rows')
        for row in rows:
            if not isinstance(row, dict) or set(row) != keys or any(not isinstance(v, str) or len(v.encode()) > 2048 for v in row.values()):
                raise ValueError('invalid feedback text')
            if any(_redact_feedback(v) != v for v in row.values()):
                raise ValueError('feedback contains secret-shaped text')
    digests = value['review_input_digests']
    if not isinstance(digests, list) or len(digests) > 2 or any(not isinstance(d, str) or not re.fullmatch('[0-9a-f]{64}', d) for d in digests):
        raise ValueError('invalid review input digest')
    release = value['release']
    if value['status'] == 'released':
        if (not isinstance(release, dict) or set(release) != {'pr_number', 'merge_sha', 'main_sha', 'hosts'}
                or type(release['pr_number']) is not int or release['pr_number'] <= 0
                or not all(isinstance(release[k], str) and SHA.fullmatch(release[k]) for k in ('merge_sha', 'main_sha'))
                or not isinstance(release['hosts'], dict) or set(release['hosts']) != {'app', 'broker'}
                or not all(v is True for v in release['hosts'].values())):
            raise ValueError('release feedback requires verified both-host receipts')
    elif release is not None:
        raise ValueError('unexpected release receipt')
    raw = json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()
    if len(raw) > MAX_FEEDBACK_BYTES:
        raise ValueError('feedback exceeds size limit')
    return raw


def _make_feedback(base, head, status, *, checks=(), receipts=(), release=None, diagnostics=()):
    findings = [{'reviewer': _redact_feedback(r.reviewer, 128), 'message': _redact_feedback(f)}
                for r in receipts for f in getattr(r, 'findings', ())][:20]
    findings.extend({'reviewer': 'controller', 'message': _redact_feedback(message)} for message in list(diagnostics)[:20])
    value = {'schema': 1, 'base_sha': base, 'head_sha': head, 'status': status,
             'checks': [{'name': _redact_feedback(c.name, 256), 'status': _redact_feedback(c.status, 32),
                         'conclusion': _redact_feedback(c.conclusion, 32)} for c in list(checks)[:100]],
             'findings': findings, 'review_input_digests': sorted({r.review_input_digest for r in receipts if getattr(r, 'review_input_digest', '')})[:2], 'release': release}
    # Keep transport bounded even when JSON escaping expands diagnostic text.
    while len(json.dumps(value, ensure_ascii=False).encode()) > MAX_FEEDBACK_BYTES:
        if value['findings']: value['findings'].pop()
        elif value['checks']: value['checks'].pop()
        else: raise ValueError('feedback exceeds size limit')
    _validate_feedback(value, base, head)
    return value


def _ci_diagnostics(page, repository, head):
    """Copy bounded failure tails through the operator, never its credentials."""
    import selectors
    import time
    if repository not in (PUBLIC, PRIVATE): raise ValueError('invalid diagnostic repository')
    messages = []
    failed = [c for c in page.get('check_runs', []) if c.get('head_sha') == head and c.get('status') == 'completed'
              and c.get('conclusion') not in ('success', 'neutral', 'skipped')][:3]
    for check in failed:
        output = check.get('output') or {}
        for name in ('title', 'summary', 'text'):
            if output.get(name): messages.append(f"{check['name']}: {output[name]}")
        job = re.fullmatch(r'https://github\.com/' + re.escape(repository) + r'/actions/runs/[0-9]+/job/([0-9]+)', check.get('details_url', ''))
        if not job: continue
        tail = b''
        with subprocess.Popen(['/opt/homebrew/bin/gh', 'api', '--allow-escape-sequences', f'repos/{repository}/actions/jobs/{job[1]}/logs'],
                              stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as child:
            try:
                with selectors.DefaultSelector() as ready:
                    ready.register(child.stdout, selectors.EVENT_READ)
                    deadline = time.monotonic() + 15
                    while ready.get_map():
                        remaining = deadline - time.monotonic()
                        if remaining <= 0: raise TimeoutError('CI diagnostic read timed out')
                        for key, _ in ready.select(remaining):
                            chunk = os.read(key.fd, 65536)
                            if not chunk: ready.unregister(key.fileobj)
                            else: tail = (tail + chunk)[-16384:]
                if child.wait(timeout=1): raise RuntimeError('CI diagnostics unavailable')
                text = re.sub(r'\x1b(?:\][^\x07\x1b]*(?:\x07|\x1b\\)|\[[0-?]*[ -/]*[@-~]|[@-_])', '', tail.decode(errors='replace'))
                text = _redact_feedback(re.sub(r'[\x00-\x08\x0b-\x1f\x7f-\x9f]', '', text), 16384)
                messages.extend(f"{check['name']} bounded CI log tail: {text[i:i+1800]}" for i in range(0, len(text), 1800))
            except (OSError, RuntimeError, subprocess.TimeoutExpired):
                child.kill(); child.wait()
                messages.append(f"{check['name']}: CI log unavailable; reproduce the exact-head suite locally.")
    return messages[:20]


def poll_feedback(base, head, *, cwd):
    """Read one fixed data blob with the bot's existing archive deploy key."""
    ref = _feedback_ref(base, head)
    _require_private_archive()
    with tempfile.TemporaryDirectory(prefix='private-feedback-') as temporary:
        env = _archive_git_environment(Path(temporary))
        def git(*args, check=True):
            return subprocess.run(['/usr/bin/git', '-c', 'core.hooksPath=/dev/null', '-c', 'core.fsmonitor=false', *args],
                                  cwd=temporary, env=env, check=check, capture_output=True, timeout=180)
        found = git('ls-remote', '--exit-code', f'git@github.com:{PRIVATE}.git', ref, check=False)
        if found.returncode == 2:
            return {'status': 'private-validation-pending', 'head_sha': head}
        found.check_returncode()
        rows = found.stdout.decode().splitlines()
        if len(rows) != 1 or rows[0].split()[-1] != ref or not SHA.fullmatch(rows[0].split()[0]):
            raise ValueError('invalid private feedback ref')
        commit = rows[0].split()[0]
        git('init', '--bare', '.')
        git('-c', 'fetch.fsckObjects=true', '-c', 'transfer.fsckObjects=true', 'fetch', '--no-tags', '--depth=1', f'git@github.com:{PRIVATE}.git', commit)
        if int(git('cat-file', '-s', commit).stdout) > 4096: raise ValueError('feedback commit metadata exceeds limit')
        headers = git('cat-file', '-p', commit).stdout.split(b'\n\n', 1)[0].splitlines()
        if any(line.startswith(b'parent ') for line in headers):
            raise ValueError('feedback commit must be orphaned')
        tree_sha = next((line.split()[1].decode() for line in headers if line.startswith(b'tree ')), '')
        if not SHA.fullmatch(tree_sha) or int(git('cat-file', '-s', tree_sha).stdout) > 1024: raise ValueError('feedback tree exceeds limit')
        tree = git('ls-tree', '-z', commit).stdout
        match = re.fullmatch(rb'100644 blob ([0-9a-f]{40})\tfeedback\.json\x00', tree)
        if not match:
            raise ValueError('feedback tree must contain one fixed regular blob')
        blob = match.group(1).decode()
        size = int(git('cat-file', '-s', blob).stdout)
        if size > MAX_FEEDBACK_BYTES:
            raise ValueError('feedback exceeds size limit')
        value = json.loads(git('cat-file', 'blob', blob).stdout)
        _validate_feedback(value, base, head)
        return value


def _require_private_archive():
    import urllib.error
    import urllib.request
    try:
        urllib.request.urlopen(f'https://api.github.com/repos/{PRIVATE}', timeout=30).close()
    except urllib.error.HTTPError as error:
        if error.code != 404:
            raise RuntimeError('private archive visibility is unverifiable') from error
    else:
        raise PermissionError('security archive is publicly visible')


def _archive_git_environment(temporary):
    key = Path.home() / '.radon-runner-reports-key'
    if not key.is_file() or key.is_symlink():
        raise PermissionError('private report deploy key unavailable')
    hosts = temporary / 'known_hosts'
    hosts.write_text('github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl\n')
    import shlex
    return {'HOME': str(Path.home()), 'PATH': '/opt/homebrew/bin:/usr/bin:/bin', 'GIT_TERMINAL_PROMPT': '0',
            'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null',
            'GIT_SSH_COMMAND': '/usr/bin/ssh -i ' + shlex.quote(str(key)) + ' -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=' + shlex.quote(str(hosts))}

def export_candidate(base, head, *, severity, cwd):
    if severity != 'P1' or not SHA.fullmatch(base) or not SHA.fullmatch(head):
        raise ValueError('only immutable P1 candidates can use standing release policy')
    _require_private_archive()
    with tempfile.TemporaryDirectory(prefix='private-release-') as temporary:
        environment = _archive_git_environment(Path(temporary))
        def git(*args, capture=False):
            return subprocess.run(['/usr/bin/git', '-c', 'core.hooksPath=/dev/null', '-c', 'core.fsmonitor=false', *args], cwd=cwd, env=environment, check=True, capture_output=True, text=capture or None, timeout=180)
        git('merge-base', '--is-ancestor', base, head)
        # The release path pushes `head` itself into the PUBLIC repository with
        # no depth restriction, while review covers only the aggregate
        # base...head diff and its changed blobs. Content committed in an
        # intermediate commit and removed again before the final tree would
        # stay permanently reachable in public git history. A squash would
        # break the deliberate SHA pinning the whole pipeline rests on, so a
        # candidate that is more than one commit ahead of base is refused and
        # must be reduced to a single commit first.
        ahead = (git('rev-list', '--count', f'{base}..{head}', capture=True).stdout or '').strip()
        if ahead != '1':
            raise ValueError(f'candidate must be exactly one commit ahead of base, not {ahead or "unknown"}: every intermediate commit would become public history')
        git('push', '--atomic', f'git@github.com:{PRIVATE}.git', f'{base}:refs/heads/validation/base/{base}', f'{head}:refs/heads/validation/fix/{base}/{head}')
    return {'status': 'private-validation-pending', 'head_sha': head}


class PrivateReleaseBridge:
    def __init__(self, github, reviewer, policy, state_directory, *, on_public_green=None):
        self.github, self.reviewer, self.policy = github, reviewer, policy
        self.directory = Path(state_directory)
        self.directory.mkdir(mode=0o700, exist_ok=True)
        self.priority_pr_number = None
        self.priority_head_sha = None
        self.on_public_green = on_public_green

    def _check_failure(self, snapshot, pr):
        if snapshot.head_sha != pr.head_sha:
            return True
        accepted = ('SUCCESS', 'SKIPPED', 'NEUTRAL')
        for check in snapshot.checks:
            if check.head_sha != pr.head_sha:
                return True
            if check.status.upper() == 'COMPLETED' and (check.conclusion or '').upper() not in accepted:
                return True
        by_name = {check.name: check for check in snapshot.checks}
        for name in self.policy.required_checks:
            check = by_name.get(name)
            if check and check.status.upper() == 'COMPLETED' and (check.conclusion or '').upper() != 'SUCCESS':
                return True
        return False

    def _published_pr(self, head, marker):
        import re
        data = json.loads(marker.read_text())
        url = data.get('public_pr', '')
        match = re.fullmatch(r'https://github\.com/joemccann/radon/pull/(\d+)', url)
        if not match:
            return 'private-validation-failed'
        number = int(match.group(1))
        row = self._api(f'repos/{PUBLIC}/pulls/{number}')
        if row.get('head', {}).get('sha') != head or row.get('base', {}).get('ref') != 'main':
            self._finish_marker(marker, 'public-head-changed')
            return 'private-validation-failed'
        if row.get('merged') is True:
            self._finish_marker(marker, 'merged')
            return 'waiting'
        if row.get('state') != 'open':
            self._finish_marker(marker, 'public-pr-closed')
            return 'private-validation-failed'
        base = row.get('base', {}).get('sha')
        checks = self._api(f'repos/{PUBLIC}/commits/{head}/check-runs?per_page=100')
        from unattended_release import Check, ChecksSnapshot, PullRequest, _checks_green
        pr = PullRequest(number, head, f'security-private-release/{head}', repository=PUBLIC, base_sha=base)
        statuses = self._api(f'repos/{PUBLIC}/commits/{head}/status?per_page=100')
        rows, count = statuses.get('statuses'), statuses.get('total_count')
        if (checks.get('total_count', 0) > 100 or statuses.get('sha') != head
                or not isinstance(rows, list) or type(count) is not int or count > 100 or count != len(rows)):
            self._finish_marker(marker, 'public-checks-invalid')
            return 'private-validation-failed'
        result = [Check(c['name'], c['status'], c['conclusion'], c['head_sha']) for c in checks.get('check_runs', [])]
        result.extend(Check(c['context'], 'COMPLETED' if c['state'] in ('success', 'failure', 'error') else 'PENDING',
                            'SUCCESS' if c['state'] == 'success' else c['state'].upper(), head) for c in rows)
        snapshot = ChecksSnapshot(head, self.policy.required_checks, result)
        if not _checks_green(snapshot, pr, self.policy):
            if self._check_failure(snapshot, pr):
                self._finish_marker(marker, 'public-checks-failed', checks=snapshot.checks, diagnostics=_ci_diagnostics(checks, PUBLIC, head))
                return 'private-validation-failed'
            return 'public-ci-pending'
        if self.on_public_green is not None:
            self.on_public_green(number, head)
        comparison = self._api(f'repos/{PUBLIC}/compare/{base}...{head}')
        if comparison.get('status') != 'ahead' or comparison.get('merge_base_commit', {}).get('sha') != base or len(comparison.get('files', [])) >= 300:
            self._finish_marker(marker, 'public-diff-invalid')
            return 'private-validation-failed'
        diff = subprocess.run(['/opt/homebrew/bin/gh', 'api', f'repos/{PUBLIC}/compare/{base}...{head}', '-H', 'Accept: application/vnd.github.diff'], check=True, capture_output=True, text=True, timeout=120).stdout
        pr = PullRequest(number, head, f'security-private-release/{head}', diff_digest=hashlib.sha256(diff.encode()).hexdigest(), repository=PUBLIC, base_sha=base)
        receipts = self.reviewer.review(pr, diff, repository=pr.repository, base_sha=base)
        if any(r.reviewer in self.policy.reviewers and r.head_sha == head and r.diff_digest == pr.diff_digest and r.authenticated and not r.approved for r in receipts):
            self._finish_marker(marker, 'public-review-rejected', receipts=receipts)
            return 'private-validation-failed'
        if not all(any(r.reviewer == model and r.head_sha == head and r.diff_digest == pr.diff_digest and r.authenticated and r.approved for r in receipts) for model in self.policy.reviewers):
            self._finish_marker(marker, 'public-review-invalid', receipts=receipts, diagnostics=self._review_failure())
            return 'private-validation-failed'
        self.priority_pr_number = number
        self.priority_head_sha = head
        return 'public-ready'

    def _finish_marker(self, marker, status, *, checks=(), receipts=(), diagnostics=()):
        data = json.loads(marker.read_text())
        if status == 'merged':
            data['status'] = status
            self._replace_marker(marker, data)
        else:
            self._terminal_marker(marker, data['base_sha'], data['head_sha'], status, checks=checks, receipts=receipts, diagnostics=diagnostics)

    def _review_failure(self):
        reason = getattr(self.reviewer, 'failure_reason', '')
        return (reason or 'Independent review incomplete: missing authenticated exact-input receipts. Verify source context and reviewer readiness; preserve the incomplete checkpoint.',)

    def _terminal_marker(self, marker, base, head, status, *, checks=(), receipts=(), diagnostics=()):
        value = {'head_sha': head, 'base_sha': base, 'status': status,
                 'feedback': _make_feedback(base, head, status, checks=checks, receipts=receipts, diagnostics=diagnostics)}
        if marker.exists():
            data = json.loads(marker.read_text()); data.update(value)
            self._replace_marker(marker, data)
        else:
            self._save_marker(marker, value)

    @staticmethod
    def _replace_marker(marker, data):
        fd, temporary = tempfile.mkstemp(prefix=marker.name + '.', dir=marker.parent)
        try:
            with os.fdopen(fd, 'w') as stream:
                json.dump(data, stream); stream.flush(); os.fsync(stream.fileno())
            os.replace(temporary, marker)
            parent_fd = os.open(marker.parent, os.O_RDONLY)
            try: os.fsync(parent_fd)
            finally: os.close(parent_fd)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def _read_feedback(self, commit, base, head):
        import base64
        if not SHA.fullmatch(commit): raise ValueError('invalid feedback commit')
        row = self._api(f'repos/{PRIVATE}/git/commits/{commit}')
        if row.get('parents') != []: raise ValueError('feedback commit must be orphaned')
        tree_sha = row.get('tree', {}).get('sha', '')
        if not isinstance(tree_sha, str) or not SHA.fullmatch(tree_sha): raise ValueError('invalid feedback tree identity')
        tree = self._api(f'repos/{PRIVATE}/git/trees/{tree_sha}')
        entries = tree.get('tree', [])
        if (tree.get('truncated') is not False or len(entries) != 1 or entries[0].get('path') != 'feedback.json'
                or entries[0].get('mode') != '100644' or entries[0].get('type') != 'blob'
                or not SHA.fullmatch(entries[0].get('sha', ''))):
            raise ValueError('feedback tree must contain one fixed regular blob')
        blob = self._api(f'repos/{PRIVATE}/git/blobs/{entries[0]["sha"]}')
        if type(blob.get('size')) is not int or blob['size'] > MAX_FEEDBACK_BYTES or blob.get('encoding') != 'base64':
            raise ValueError('invalid feedback blob')
        raw = base64.b64decode(blob['content'])
        if len(raw) != blob['size'] or len(raw) > MAX_FEEDBACK_BYTES: raise ValueError('invalid feedback size')
        value = json.loads(raw)
        _validate_feedback(value, base, head)
        return value

    def flush_feedback(self, releases):
        # The controller calls this under its owner-only state lock, including
        # retries after CI/review/transport errors. Feedback grants no authority.
        for record in releases.values():
            head = record.get('head_sha', '')
            if record.get('status') != 'green' or not isinstance(head, str) or not SHA.fullmatch(head): continue
            marker = self.directory / f'{head}.json'
            if not marker.is_file() or marker.is_symlink(): continue
            data = json.loads(marker.read_text())
            if data.get('head_sha') != head or data.get('feedback') is not None or data.get('public_pr') != f'https://github.com/{PUBLIC}/pull/{record.get("pr_number")}': continue
            synced = record.get('sync_receipt', {})
            release = {'pr_number': record.get('pr_number'), 'merge_sha': record.get('merge_sha'),
                       'main_sha': synced.get('main_sha'), 'hosts': synced.get('hosts')}
            try: feedback = _make_feedback(data.get('base_sha'), head, 'released', release=release)
            except ValueError: continue
            data.update(status='released', feedback=feedback)
            self._replace_marker(marker, data)
        for marker in sorted(self.directory.glob('*.json')):
            if marker.is_symlink() or not SHA.fullmatch(marker.stem): raise ValueError('unsafe feedback marker')
            data = json.loads(marker.read_text())
            value = data.get('feedback')
            if value is None or data.get('feedback_published'): continue
            base, head = data.get('base_sha'), data.get('head_sha')
            if marker.stem != head: raise ValueError('feedback marker head mismatch')
            raw = _validate_feedback(value, base, head).decode()
            if self._api(f'repos/{PRIVATE}').get('private') is not True: raise PermissionError('feedback repository is not private')
            ref = _feedback_ref(base, head)
            try: existing = self._api(f'repos/{PRIVATE}/git/ref/{ref.removeprefix("refs/")}')['object']['sha']
            except RuntimeError: existing = None
            if existing is None:
                blob = self._api(f'repos/{PRIVATE}/git/blobs', '--method', 'POST', payload={'content': raw, 'encoding': 'utf-8'})['sha']
                tree = self._api(f'repos/{PRIVATE}/git/trees', '--method', 'POST', payload={'tree': [{'path': 'feedback.json', 'mode': '100644', 'type': 'blob', 'sha': blob}]})['sha']
                commit = self._api(f'repos/{PRIVATE}/git/commits', '--method', 'POST', payload={'message': 'Private validation feedback', 'tree': tree, 'parents': []})['sha']
                try: self._api(f'repos/{PRIVATE}/git/refs', '--method', 'POST', payload={'ref': ref, 'sha': commit})
                except RuntimeError:
                    existing = self._api(f'repos/{PRIVATE}/git/ref/{ref.removeprefix("refs/")}')['object']['sha']
                else: existing = commit
            if self._read_feedback(existing, base, head) != value: raise PermissionError('immutable feedback ref differs')
            data['feedback_published'] = existing
            self._replace_marker(marker, data)

    def _save_marker(self, marker, value):
        fd, temporary = tempfile.mkstemp(prefix=marker.name + '.', dir=marker.parent)
        try:
            with os.fdopen(fd, 'w') as stream:
                json.dump(value, stream); stream.flush(); os.fsync(stream.fileno())
            os.link(temporary, marker, follow_symlinks=False)
            os.unlink(temporary)
            parent_fd = os.open(marker.parent, os.O_RDONLY)
            try: os.fsync(parent_fd)
            finally: os.close(parent_fd)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def _api(self, path, *args, **kwargs):
        return self.github.api(path, *args, **kwargs)

    def _validate_body(self, body):
        import sys
        with tempfile.TemporaryDirectory(prefix='pr-body-', dir=self.directory) as temporary:
            path = Path(temporary) / 'body.md'
            path.write_text(body)
            subprocess.run([sys.executable, '-I', str(Path(__file__).parent / 'ci/check_pr_body.py'), '--body-file', str(path), '--author', 'joemccann'], check=True, capture_output=True, text=True, timeout=30)

    @staticmethod
    def _checklist():
        return "## Checklist\n" + "\n".join('- [ ] ' + item for item in (
            'Red/green TDD (failing test, then fix)',
            'Stage only the files this change owns (no `git add -A`)',
            'Owner doc updated, or the commit message has a `docs-skip:` reason',
            'Previous main deploy finished before this push'))

    def _push_validation_workflows(self, archive_head, changed):
        """Update fixed workflow blobs through the operator's existing SSH identity."""
        paths = [entry['path'] for entry in changed]
        if (not SHA.fullmatch(archive_head) or not changed or len(paths) != len(set(paths))
                or any(path not in WORKFLOW_PATHS for path in paths)):
            raise ValueError('invalid private workflow update')
        environment = {'HOME': str(Path.home()), 'PATH': '/opt/homebrew/bin:/usr/bin:/bin',
                       'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null',
                       'GIT_TERMINAL_PROMPT': '0',
                       'GIT_SSH_COMMAND': '/usr/bin/ssh -o BatchMode=yes -o StrictHostKeyChecking=yes'}
        if os.environ.get('SSH_AUTH_SOCK'):
            environment['SSH_AUTH_SOCK'] = os.environ['SSH_AUTH_SOCK']
        remote = f'git@github.com:{PRIVATE}.git'
        with tempfile.TemporaryDirectory(prefix='workflow-bootstrap-', dir=self.directory) as temporary:
            def git(*args, input=None):
                return subprocess.run(['/usr/bin/git', '-c', 'core.hooksPath=/dev/null',
                                       '-c', 'core.fsmonitor=false', '-c', 'fetch.fsckObjects=true',
                                       '-c', 'transfer.fsckObjects=true', *args], cwd=temporary,
                                      env=environment, input=input, check=True, capture_output=True,
                                      text=True, timeout=180).stdout.strip()
            git('init', '--bare', '.')
            git('fetch', '--no-tags', '--no-recurse-submodules', remote, 'refs/heads/main')
            if git('rev-parse', 'FETCH_HEAD') != archive_head:
                raise RuntimeError('private archive main changed before workflow staging')
            # Git index plumbing retains every report without checking out or
            # running private files, attributes, hooks, or candidate source.
            git('read-tree', archive_head)
            for entry in changed:
                blob = git('hash-object', '-w', '--stdin', input=entry['content'])
                git('update-index', '--add', '--cacheinfo', f'100644,{blob},{entry["path"]}')
            tree = git('write-tree')
            commit = git('-c', 'user.name=Radon operator', '-c', 'user.email=radon-operator@users.noreply.github.com',
                         'commit-tree', tree, '-p', archive_head, '-m', 'Maintain private validation workflows')
            # A concurrent archive writer rejects this non-force push. A later
            # tick refetches the new parent instead of overwriting its reports.
            git('push', remote, f'{commit}:refs/heads/main')
            return commit

    def ensure_validation_workflows(self, source_sha):
        """Register vetted PR-only workflows without running CI on report pushes."""
        import base64
        if not SHA.fullmatch(source_sha):
            raise ValueError('immutable public workflow source required')
        paths = WORKFLOW_PATHS
        archive_head = self._api(f'repos/{PRIVATE}/git/ref/heads/main')['object']['sha']
        tree = self._api(f'repos/{PRIVATE}/git/commits/{archive_head}')['tree']['sha']
        entries = self._api(f'repos/{PRIVATE}/git/trees/{tree}?recursive=1')
        if entries.get('truncated'): raise RuntimeError('private workflow tree is truncated')
        existing = {entry['path']: entry['sha'] for entry in entries['tree']}
        changed = []
        for path in paths:
            content = base64.b64decode(self._api(f'repos/{PUBLIC}/contents/{path}?ref={source_sha}')['content']).decode()
            if path.endswith('/ci.yml'):
                old = 'on:\n  pull_request:\n  push:\n    branches: [main]'
                new = "on:\n  pull_request:\n    branches: ['validation/base/**']"
                if old not in content: raise RuntimeError('unrecognized public CI event contract')
                content = content.replace(old, new, 1)
            encoded = content.encode()
            blob = hashlib.sha1(f'blob {len(encoded)}\0'.encode() + encoded).hexdigest()
            if existing.get(path) != blob:
                changed.append({'path': path, 'mode': '100644', 'type': 'blob', 'content': content})
        # These values are deliberately shipped in public browser bundles.
        # Copy only this fixed allowlist; never enumerate or copy secrets.
        import urllib.parse
        public_values = {}
        for name in ('NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY', 'NEXT_PUBLIC_RADON_API_URL', 'NEXT_PUBLIC_IB_REALTIME_WS_URL'):
            value = self._api(f'repos/{PUBLIC}/actions/variables/{name}')['value']
            if name == 'NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY':
                if not value.startswith(('pk_test_', 'pk_live_')): raise ValueError('invalid public Clerk build key')
            else:
                parsed = urllib.parse.urlsplit(value)
                if parsed.scheme not in ('https', 'wss') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
                    raise ValueError('invalid public build URL')
            public_values[name] = value
        for name, value in public_values.items():
            try:
                existing_value = self._api(f'repos/{PRIVATE}/actions/variables/{name}').get('value')
            except RuntimeError:
                self._api(f'repos/{PRIVATE}/actions/variables', '--method', 'POST', payload={'name': name, 'value': value})
            else:
                if existing_value != value:
                    self._api(f'repos/{PRIVATE}/actions/variables/{name}', '--method', 'PATCH', payload={'name': name, 'value': value})
        if not changed: return
        permissions = self._api(f'repos/{PRIVATE}/actions/permissions')
        if permissions.get('enabled') is not True:
            raise RuntimeError('private validation Actions are disabled')
        self._push_validation_workflows(archive_head, changed)

    def run_once(self):
        # API permissions are verified every cycle; a renamed/public archive
        # must never become the destination for a sensitive candidate.
        if self._api(f'repos/{PRIVATE}').get('private') is not True:
            raise PermissionError('security validation repository is not private')
        refs = self._api(f'repos/{PRIVATE}/git/matching-refs/heads/validation/fix/')
        main = self.github.main_sha()
        terminal_failure = False
        for ref in refs:
            match = re.fullmatch(r'refs/heads/validation/fix/([0-9a-f]{40})/([0-9a-f]{40})', ref['ref'])
            if not match: continue
            base, head = match.groups()
            if ref['object']['sha'] != head or base != main: continue
            self.ensure_validation_workflows(base)
            marker = self.directory / f'{head}.json'
            if marker.exists():
                marker_status = json.loads(marker.read_text()).get('status')
                if marker_status in {'private-checks-failed', 'private-review-rejected', 'private-review-invalid',
                                     'public-checks-invalid', 'public-checks-failed', 'public-head-changed',
                                     'public-pr-closed', 'public-diff-invalid', 'public-review-rejected',
                                     'public-review-invalid', 'merged', 'released'}:
                    terminal_failure = True
                    continue
                result = self._published_pr(head, marker)
                if result == 'public-ready':
                    return result
                if result == 'private-validation-failed':
                    terminal_failure = True
                    continue
                if result != 'waiting':
                    return result
                continue
            head_branch, base_branch = ref['ref'].removeprefix('refs/heads/'), f'validation/base/{base}'
            prs = self._api(f'repos/{PRIVATE}/pulls?state=open&head=joemccann:{head_branch}')
            if not prs:
                body = '\n\n'.join(['## Summary\nPrivate automated validation.', '## What changed\nImmutable candidate from the security loop.', '## Test plan\n- [ ] Exact-head CI and independent Sol/Luna review.', '## Risk and rollback\nPublic release remains gated. Revert the eventual merge if needed.', '## Operator Next Steps\nNone', self._checklist()])
                self._validate_body(body)
                self._api(f'repos/{PRIVATE}/pulls', '--method', 'POST', '-f', 'title=Private maintenance validation', '-f', f'head={head_branch}', '-f', f'base={base_branch}', '-f', f'body={body}')
                return 'private-ci-pending'
            row = prs[0]
            if row['head']['sha'] != head or row['base']['sha'] != base: continue
            checks = self._api(f'repos/{PRIVATE}/commits/{head}/check-runs?per_page=100')
            from unattended_release import Check, ChecksSnapshot, PullRequest, _checks_green
            pr = PullRequest(row['number'], head, head_branch, repository=PRIVATE, base_sha=base)
            snapshot = ChecksSnapshot(head, self.policy.required_checks, [Check(c['name'], c['status'], c['conclusion'], c['head_sha']) for c in checks['check_runs']])
            if checks.get('total_count', 0) > 100:
                continue
            if not _checks_green(snapshot, pr, self.policy):
                if self._check_failure(snapshot, pr):
                    self._terminal_marker(marker, base, head, 'private-checks-failed', checks=snapshot.checks, diagnostics=_ci_diagnostics(checks, PRIVATE, head))
                    terminal_failure = True
                    continue
                return 'private-ci-pending'
            comparison = self._api(f'repos/{PRIVATE}/compare/{base}...{head}')
            if comparison.get('status') != 'ahead' or comparison.get('merge_base_commit', {}).get('sha') != base: continue
            # Same reason as export_candidate: _publish transfers the head
            # commit's whole ancestry into the public repository.
            if comparison.get('ahead_by') != 1:
                self._terminal_marker(marker, base, head, 'private-review-invalid', diagnostics=(
                    'Candidate must be exactly one commit ahead of its recorded base. '
                    'Reduce the repair to one independently tested commit and export the new head; '
                    'intermediate private commits must never enter public history.',))
                terminal_failure = True
                continue
            if len(comparison.get('files', [])) >= 300: continue
            diff = subprocess.run(['/opt/homebrew/bin/gh', 'api', f'repos/{PRIVATE}/compare/{base}...{head}', '-H', 'Accept: application/vnd.github.diff'], check=True, capture_output=True, text=True, timeout=120).stdout
            pr = PullRequest(row['number'], head, head_branch, diff_digest=hashlib.sha256(diff.encode()).hexdigest(), repository=PRIVATE, base_sha=base)
            receipts = self.reviewer.review(pr, diff, repository=pr.repository, base_sha=base)
            rejected = any(r.reviewer in self.policy.reviewers and r.head_sha == head and r.diff_digest == pr.diff_digest and r.authenticated and not r.approved for r in receipts)
            if rejected:
                self._terminal_marker(marker, base, head, 'private-review-rejected', receipts=receipts)
                terminal_failure = True
                continue
            if not all(any(r.reviewer == model and r.approved and r.authenticated and r.head_sha == head and r.diff_digest == pr.diff_digest for r in receipts) for model in self.policy.reviewers):
                self._terminal_marker(marker, base, head, 'private-review-invalid', receipts=receipts, diagnostics=self._review_failure())
                terminal_failure = True
                continue
            # Re-read every authority-bearing ref after the potentially long
            # reviews. The public object transfer is pinned to the reviewed SHA.
            if self.github.main_sha() != base: continue
            fresh = self._api(f'repos/{PRIVATE}/pulls/{row["number"]}')
            if fresh['head']['sha'] != head or fresh['base']['sha'] != base: continue
            fresh_checks = self._api(f'repos/{PRIVATE}/commits/{head}/check-runs?per_page=100')
            fresh_snapshot = ChecksSnapshot(head, self.policy.required_checks, [Check(c['name'], c['status'], c['conclusion'], c['head_sha']) for c in fresh_checks['check_runs']])
            if fresh_checks.get('total_count', 0) > 100 or not _checks_green(fresh_snapshot, pr, self.policy): continue
            branch = f'security-private-release/{head}'
            url = self._publish(head, branch)
            self._save_marker(marker, {'head_sha': head, 'base_sha': base, 'public_pr': url, 'status': 'public-ci-pending'})
            return 'public-ci-pending'
        return 'private-validation-failed' if terminal_failure else 'waiting'

    def _publish(self, head, branch):
        # No checkout, hooks, candidate scripts or credentials enter the review
        # process. Git only transfers immutable objects between fixed repos.
        env = {'HOME': str(Path.home()), 'PATH': '/opt/homebrew/bin:/usr/bin:/bin', 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null'}
        with tempfile.TemporaryDirectory(prefix='publish-', dir=self.directory) as temporary:
            def git(*args):
                subprocess.run(['/usr/bin/git', '-c', 'core.hooksPath=/dev/null', '-c', 'core.fsmonitor=false', '-c', 'credential.helper=!/opt/homebrew/bin/gh auth git-credential', *args], cwd=temporary, env=env, check=True, capture_output=True, timeout=180)
            git('init', '--bare', '.')
            git('fetch', '--no-tags', f'https://github.com/{PRIVATE}.git', head)
            git('push', f'https://github.com/{PUBLIC}.git', f'{head}:refs/heads/{branch}')
        existing = self._api(f'repos/{PUBLIC}/pulls?state=all&head=joemccann:{branch}')
        if existing: return existing[0]['html_url']
        body = '\n\n'.join(['## Summary\nShip a privately validated maintenance correction.', '## What changed\nPreserve the independently reviewed candidate unchanged.', '## Test plan\n- [x] Private exact-head CI and independent Sol/Luna review.\n- [ ] Public exact-head CI.', '## Risk and rollback\nRevert the merge commit to restore the prior release.', '## Operator Next Steps\nNone. The installed release coordinator owns merge and host reconciliation.', self._checklist()])
        self._validate_body(body)
        return self._api(f'repos/{PUBLIC}/pulls', '--method', 'POST', '-f', 'title=Apply validated maintenance correction', '-f', f'head={branch}', '-f', 'base=main', '-f', f'body={body}')['html_url']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', required=True)
    parser.add_argument('--head', required=True)
    parser.add_argument('--severity', choices=['P1'])
    parser.add_argument('--status', action='store_true')
    args = parser.parse_args()
    result = poll_feedback(args.base, args.head, cwd=Path.cwd()) if args.status else export_candidate(args.base, args.head, severity=args.severity, cwd=Path.cwd())
    print(json.dumps(result))


if __name__ == '__main__':
    main()
