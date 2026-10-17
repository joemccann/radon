"""Observe a pinned CLI's actual model request before granting live review access."""
from __future__ import annotations
import hashlib
import http.server
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import tempfile
import threading


def _private(path):
    for item in (path, *path.parents):
        info = item.lstat()
        if stat.S_ISLNK(info.st_mode) or info.st_uid not in (0, os.getuid()):
            raise PermissionError('untrusted reviewer snapshot path')
        if info.st_mode & 0o022 and not (item != path and info.st_mode & stat.S_ISVTX):
            raise PermissionError('writable reviewer snapshot path')


def snapshot_binary(source: Path, directory: Path) -> Path:
    """Pin vendor-signed bytes independently of Homebrew's mutable ancestors."""
    source = Path(source).resolve(strict=True)
    _private(directory)
    snapshots = directory / 'reviewer-binaries'
    snapshots.mkdir(mode=0o700, exist_ok=True); _private(snapshots)
    candidate = Path(tempfile.mkdtemp(prefix='.candidate-', dir=snapshots))
    try:
        fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, 'rb') as original:
            info = os.fstat(original.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid not in (0, os.getuid()) or info.st_mode & 0o022 or not info.st_mode & 0o111:
                raise PermissionError('untrusted reviewer executable bytes')
            copied = candidate / 'codex'
            with copied.open('xb') as output:
                shutil.copyfileobj(original, output)
                output.flush(); os.fsync(output.fileno())
        copied.chmod(0o500)
        requirement = '=anchor apple generic and identifier "codex" and certificate leaf[subject.OU] = "2DC432GLL2"'
        verified = subprocess.run(['/usr/bin/codesign', '--verify', '--strict', '--all-architectures', '-R', requirement, str(copied)],
                                  env={'PATH': '/usr/bin:/bin'}, stdin=subprocess.DEVNULL, capture_output=True, timeout=30)
        if verified.returncode: raise RuntimeError('reviewer vendor signature verification failed')
        digest = hashlib.sha256(copied.read_bytes()).hexdigest()
        target = snapshots / digest
        if target.exists() or target.is_symlink():
            existing = target / 'codex'; _private(existing)
            if not existing.is_file() or existing.stat().st_mode & 0o777 != 0o500 or hashlib.sha256(existing.read_bytes()).hexdigest() != digest:
                raise RuntimeError('reviewer snapshot digest or permissions changed')
            return existing
        candidate_fd = os.open(candidate, os.O_RDONLY)
        try: os.fsync(candidate_fd)
        finally: os.close(candidate_fd)
        candidate.chmod(0o500); candidate.rename(target)
        parent_fd = os.open(snapshots, os.O_RDONLY)
        try: os.fsync(parent_fd)
        finally: os.close(parent_fd)
        return target / 'codex'
    finally:
        if candidate.exists():
            candidate.chmod(0o700); shutil.rmtree(candidate)


def assert_subscription(binary: Path, environment: dict[str, str]) -> None:
    result = subprocess.run([str(binary), 'login', 'status'], env=environment, stdin=subprocess.DEVNULL,
                            capture_output=True, timeout=30)
    status = (result.stdout + result.stderr).decode(errors='replace')
    if result.returncode or not re.search(r'^\s*logged in using chatgpt\s*$', status, re.I | re.M):
        raise RuntimeError('subscription reviewer login is unavailable')


def tool_free_catalog(models: list[str]) -> dict:
    """Client metadata restricts capabilities; model IDs and authentication stay intact."""
    return {'models': [dict(slug=model, display_name=model, description=None,
        base_instructions='Review only the supplied text. Return the requested structured result.',
        default_reasoning_level='low', supported_reasoning_levels=[{'effort': 'low', 'description': 'Review'}],
        shell_type='disabled', visibility='list', supported_in_api=True, priority=0,
        availability_nux=None, upgrade=None, support_verbosity=True, default_verbosity='low',
        apply_patch_tool_type=None, truncation_policy={'mode': 'tokens', 'limit': 10000},
        experimental_supported_tools=[], include_apps_usage_instructions=False,
        node_repl_disabled=True, tool_mode='direct', multi_agent_version='disabled') for model in models]}


def _has_tools(value):
    if isinstance(value, dict):
        return any((key == 'tools' and item != []) or _has_tools(item) for key, item in value.items())
    return isinstance(value, list) and any(_has_tools(item) for item in value)


def _complete_input(request, prompt):
    if request.get('truncation', 'disabled') != 'disabled' or request.get('context_management') not in (None, []):
        return False
    if request.get('previous_response_id') or request.get('conversation'):
        return False
    inputs = request.get('input')
    if isinstance(inputs, str):
        return inputs.encode() == prompt.encode()
    if not isinstance(inputs, list):
        return False
    if any(isinstance(item, dict) and item.get('type') in ('compaction', 'compaction_summary') for item in inputs):
        return False
    users = [item for item in inputs if isinstance(item, dict) and item.get('role') == 'user']
    if not users:
        return False
    content = users[-1].get('content')
    if not isinstance(content, list) or any(not isinstance(item, dict) or item.get('type') != 'input_text' or not isinstance(item.get('text'), str) for item in content):
        return False
    return ''.join(item['text'] for item in content).encode() == prompt.encode()


def assert_tool_free(binary: Path, options: list[str], directory: Path, *, prompt=None) -> None:
    """Probe full argv after binary/before prompt; preserve flags, schema and catalog bytes.

    Only output/schema/catalog/cwd locations and transport/authentication are replaced.
    Callers bind cached success to binary SHA, normalized options, schema and catalog
    bytes, and hash-check the same immutable binary again before live review.
    """
    prompt = 'Reply with a schema-conforming empty review.\n' if prompt is None else prompt
    if not isinstance(prompt, str) or not 0 < len(prompt.encode()) <= 3_100_000:
        raise ValueError('capability fixture input exceeds the complete review bound')
    binary = Path(binary)
    if not binary.is_absolute() or binary.resolve(strict=True) != binary or not binary.is_file() or not os.access(binary, os.X_OK):
        raise ValueError('capability probe requires a resolved executable binary')
    captured, invalid, attempts = [], [], []

    class Handler(http.server.BaseHTTPRequestHandler):
        def setup(self):
            super().setup(); self.connection.settimeout(3)

        def parse_request(self):
            parsed = super().parse_request()
            if parsed: attempts.append((self.command, self.path))
            return parsed

        def do_POST(self):
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if self.path != '/v1/responses' or not 0 < length <= 8_000_000 or self.headers.get('Content-Encoding'):
                    raise ValueError('unexpected request shape')
                request = json.loads(self.rfile.read(length))
                captured.append(isinstance(request, dict) and not _has_tools(request) and _complete_input(request, prompt))
            except (ValueError, OSError):
                invalid.append(True)
            try:
                self.send_response(400); self.send_header('Content-Type', 'application/json'); self.end_headers()
                self.wfile.write(b'{"error":{"message":"local capability fixture","type":"invalid_request_error","code":"context_length_exceeded"}}' )
            except OSError:
                pass

        def do_GET(self):
            invalid.append(True); self.send_error(400)

        def log_message(self, *args):
            pass

    with tempfile.TemporaryDirectory(prefix='capability-', dir=directory) as temporary:
        root = Path(temporary)
        workspace = root / 'workspace'; workspace.mkdir()
        home = root / 'home'; home.mkdir()
        arguments = list(options)
        schema_seen = False
        substitutions = {'-C': workspace, '--cd': workspace, '--output-last-message': root / 'result.json'}
        for index, argument in enumerate(arguments):
            if argument == '--output-schema':
                if index + 1 >= len(arguments): raise ValueError('missing reviewer schema')
                schema = root / 'schema.json'
                schema.write_bytes(Path(arguments[index + 1]).read_bytes())
                arguments[index + 1] = str(schema); schema_seen = True
            elif argument == '-c' and index + 1 < len(arguments) and arguments[index + 1].startswith('model_catalog_json='):
                catalog = root / 'catalog.json'
                catalog.write_bytes(Path(json.loads(arguments[index + 1].split('=', 1)[1])).read_bytes())
                arguments[index + 1] = 'model_catalog_json=' + json.dumps(str(catalog))
            elif argument in substitutions:
                if index + 1 >= len(arguments): raise ValueError('missing reviewer artifact path')
                arguments[index + 1] = str(substitutions[argument])
        required = {'exec', '--ignore-user-config', '--ignore-rules', '--ephemeral', '--skip-git-repo-check', '--json'}
        if not schema_seen or not required.issubset(arguments):
            raise ValueError('capability probe requires the complete structured exec options')
        server = http.server.HTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        environment = {'HOME': str(home), 'CODEX_HOME': str(home), 'PATH': '/opt/homebrew/bin:/usr/bin:/bin', 'LANG': 'C',
                       'OPENAI_API_KEY': 'sk-radon-probe-not-a-real-token', 'DISABLE_AUTOUPDATER': '1'}
        provider = f'model_providers.radon_probe={{name="local capability fixture",base_url="http://127.0.0.1:{server.server_port}/v1",wire_api="responses",env_key="OPENAI_API_KEY",request_max_retries=0,stream_max_retries=0}}'
        arguments += ['-c', 'model_provider="radon_probe"', '-c', provider, '-']
        process = None
        timed_out = False
        try:
            process = subprocess.Popen([str(binary), *arguments], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                       env=environment, cwd=workspace, start_new_session=True)
            try:
                process.communicate(prompt.encode(), timeout=20)
            except subprocess.TimeoutExpired:
                timed_out = True
                try: os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError: pass
                process.communicate(timeout=2)
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=1)
        if timed_out or invalid or attempts != [('POST', '/v1/responses')] or len(captured) != 1 or captured[0] is not True:
            raise RuntimeError('CLI capability probe did not observe exactly one request with an empty tool surface and byte-exact complete input without truncation/compaction')
