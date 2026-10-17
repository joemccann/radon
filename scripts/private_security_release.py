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


def export_candidate(base, head, *, severity, cwd):
    if severity != 'P1' or not SHA.fullmatch(base) or not SHA.fullmatch(head):
        raise ValueError('only immutable P1 candidates can use standing release policy')
    # An unauthenticated API lookup must be 404 for the private archive.
    # Never send source to a destination that has become publicly visible.
    import urllib.error
    import urllib.request
    try:
        urllib.request.urlopen(f'https://api.github.com/repos/{PRIVATE}', timeout=30).close()
    except urllib.error.HTTPError as error:
        if error.code != 404: raise RuntimeError('private archive visibility is unverifiable') from error
    else:
        raise PermissionError('security archive is publicly visible')
    key = Path.home() / '.radon-runner-reports-key'
    if not key.is_file() or key.is_symlink():
        raise PermissionError('private report deploy key unavailable')
    environment = {'HOME': str(Path.home()), 'PATH': '/opt/homebrew/bin:/usr/bin:/bin', 'GIT_TERMINAL_PROMPT': '0'}
    environment['GIT_CONFIG_NOSYSTEM'] = '1'
    environment['GIT_CONFIG_GLOBAL'] = '/dev/null'
    with tempfile.TemporaryDirectory(prefix='private-release-') as temporary:
        hosts = Path(temporary) / 'known_hosts'
        hosts.write_text('github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl\n')
        # File paths are fixed bot-home and generated temporary names, quoted
        # because Git passes this one variable through a shell.
        import shlex
        environment['GIT_SSH_COMMAND'] = '/usr/bin/ssh -i ' + shlex.quote(str(key)) + ' -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=' + shlex.quote(str(hosts))
        def git(*args):
            subprocess.run(['/usr/bin/git', '-c', 'core.hooksPath=/dev/null', '-c', 'core.fsmonitor=false', *args], cwd=cwd, env=environment, check=True, capture_output=True, timeout=180)
        git('merge-base', '--is-ancestor', base, head)
        git('push', '--atomic', f'git@github.com:{PRIVATE}.git', f'{base}:refs/heads/validation/base/{base}', f'{head}:refs/heads/validation/fix/{base}/{head}')
    return {'status': 'private-validation-pending', 'head_sha': head}


class PrivateReleaseBridge:
    def __init__(self, github, reviewer, policy, state_directory):
        self.github, self.reviewer, self.policy = github, reviewer, policy
        self.directory = Path(state_directory)
        self.directory.mkdir(mode=0o700, exist_ok=True)
        self.priority_pr_number = None
        self.priority_head_sha = None

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
        pr = PullRequest(number, head, f'security-private-release/{head}')
        snapshot = ChecksSnapshot(head, self.policy.required_checks,
                                  [Check(c['name'], c['status'], c['conclusion'], c['head_sha']) for c in checks.get('check_runs', [])])
        if checks.get('total_count', 0) > 100:
            self._finish_marker(marker, 'public-checks-invalid')
            return 'private-validation-failed'
        if not _checks_green(snapshot, pr, self.policy):
            if self._check_failure(snapshot, pr):
                self._finish_marker(marker, 'public-checks-failed')
                return 'private-validation-failed'
            return 'public-ci-pending'
        comparison = self._api(f'repos/{PUBLIC}/compare/{base}...{head}')
        if comparison.get('status') != 'ahead' or comparison.get('merge_base_commit', {}).get('sha') != base or len(comparison.get('files', [])) >= 300:
            self._finish_marker(marker, 'public-diff-invalid')
            return 'private-validation-failed'
        diff = subprocess.run(['/opt/homebrew/bin/gh', 'api', f'repos/{PUBLIC}/compare/{base}...{head}', '-H', 'Accept: application/vnd.github.diff'], check=True, capture_output=True, text=True, timeout=120).stdout
        pr = PullRequest(number, head, f'security-private-release/{head}', diff_digest=hashlib.sha256(diff.encode()).hexdigest())
        receipts = self.reviewer.review(pr, diff)
        if any(r.reviewer in self.policy.reviewers and r.head_sha == head and r.diff_digest == pr.diff_digest and r.authenticated and not r.approved for r in receipts):
            self._finish_marker(marker, 'public-review-rejected')
            return 'private-validation-failed'
        if not all(any(r.reviewer == model and r.head_sha == head and r.diff_digest == pr.diff_digest and r.authenticated and r.approved for r in receipts) for model in self.policy.reviewers):
            self._finish_marker(marker, 'public-review-invalid')
            return 'private-validation-failed'
        self.priority_pr_number = number
        self.priority_head_sha = head
        return 'public-ready'

    @staticmethod
    def _finish_marker(marker, status):
        data = json.loads(marker.read_text())
        data['status'] = status
        fd, temporary = tempfile.mkstemp(prefix=marker.name + '.', dir=marker.parent)
        with os.fdopen(fd, 'w') as stream:
            json.dump(data, stream); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, marker)
        parent_fd = os.open(marker.parent, os.O_RDONLY)
        try: os.fsync(parent_fd)
        finally: os.close(parent_fd)

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
                                     'public-review-invalid', 'merged'}:
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
            pr = PullRequest(row['number'], head, head_branch)
            snapshot = ChecksSnapshot(head, self.policy.required_checks, [Check(c['name'], c['status'], c['conclusion'], c['head_sha']) for c in checks['check_runs']])
            if checks.get('total_count', 0) > 100:
                continue
            if not _checks_green(snapshot, pr, self.policy):
                if self._check_failure(snapshot, pr):
                    self._save_marker(marker, {'head_sha': head, 'status': 'private-checks-failed'})
                    terminal_failure = True
                    continue
                return 'private-ci-pending'
            comparison = self._api(f'repos/{PRIVATE}/compare/{base}...{head}')
            if comparison.get('status') != 'ahead' or comparison.get('merge_base_commit', {}).get('sha') != base: continue
            if len(comparison.get('files', [])) >= 300: continue
            diff = subprocess.run(['/opt/homebrew/bin/gh', 'api', f'repos/{PRIVATE}/compare/{base}...{head}', '-H', 'Accept: application/vnd.github.diff'], check=True, capture_output=True, text=True, timeout=120).stdout
            pr = PullRequest(row['number'], head, head_branch, diff_digest=hashlib.sha256(diff.encode()).hexdigest())
            receipts = self.reviewer.review(pr, diff)
            rejected = any(r.reviewer in self.policy.reviewers and r.head_sha == head and r.diff_digest == pr.diff_digest and r.authenticated and not r.approved for r in receipts)
            if rejected:
                self._save_marker(marker, {'head_sha': head, 'status': 'private-review-rejected'})
                terminal_failure = True
                continue
            if not all(any(r.reviewer == model and r.approved and r.authenticated and r.head_sha == head and r.diff_digest == pr.diff_digest for r in receipts) for model in self.policy.reviewers):
                self._save_marker(marker, {'head_sha': head, 'status': 'private-review-invalid'})
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
            self._save_marker(marker, {'head_sha': head, 'public_pr': url, 'status': 'public-ci-pending'})
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
    parser.add_argument('--severity', choices=['P1'], required=True)
    args = parser.parse_args()
    print(json.dumps(export_candidate(args.base, args.head, severity=args.severity, cwd=Path.cwd())))


if __name__ == '__main__':
    main()
