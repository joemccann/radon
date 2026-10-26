"""Write-scoped Dropbox client for MarketDesk PDFs.

Separate from the read-only research.dropbox.DropboxClient on purpose: its own
credentials (DROPBOX_WRITE_*), an account/namespace identity check, a narrow RPC
allowlist, one content endpoint (files/upload, mode add, never overwrite) and a
single folder boundary (DROPBOX_WRITE_ROOT, default /Radon/MarketDesk).
Errors never carry tokens, request headers or Dropbox error payloads.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

from research.dropbox import DropboxError, content_hash
from research.state import MONTHS

DEFAULT_ROOT = '/Radon/MarketDesk'
TOKEN_URL = 'https://api.dropboxapi.com/oauth2/token'
RPC_BASE = 'https://api.dropboxapi.com/2/'
UPLOAD_URL = 'https://content.dropboxapi.com/2/files/upload'
MAX_UPLOAD = 100 * 1024 * 1024
RPC_ALLOWLIST = frozenset({'users/get_current_account', 'files/get_metadata'})
TIMEOUT = 45
UPLOAD_TIMEOUT = 120
NEW_YORK = ZoneInfo('America/New_York')
ARTICLE_ID_RE = re.compile(r'^[A-Za-z0-9]{8,16}$')
SLUG_MAX = 60
NAME_MAX = 200


@dataclass(frozen=True)
class UploadResult:
    path_lower: str
    id: str
    rev: str
    content_hash: str
    already_present: bool


class _FileConflict(DropboxError):
    """409 whose error_summary is path/conflict/file (a file already sits at the path)."""


def _clean_root(root):
    if not isinstance(root, str) or not root.startswith('/'):
        raise DropboxError('Invalid Dropbox write root')
    root = root.rstrip('/')
    _check_segments(root, 'Invalid Dropbox write root')
    return root


def _check_segments(path, message):
    if not path or '\\' in path or ':' in path or any(ord(c) < 32 or ord(c) == 127 for c in path):
        raise DropboxError(message)
    if any(part in ('', '.', '..') for part in path.split('/')[1:]):
        raise DropboxError(message)


class DropboxWriter:
    def __init__(self, *, app_key, refresh_token, account_id, namespace_id=None, root=DEFAULT_ROOT):
        self.app_key = app_key
        self.refresh_token = refresh_token
        self.account_id = account_id
        self.namespace_id = namespace_id or None
        self.root = _clean_root(root)
        self.token = None
        self.expires_at = 0.0
        self.verified = False

    @classmethod
    def from_env(cls, env=os.environ):
        """None only when no write credential is set (the documented pause). A partial set is a
        misconfiguration and raises, naming the missing keys and never a value."""
        required = ('DROPBOX_WRITE_APP_KEY', 'DROPBOX_WRITE_REFRESH_TOKEN', 'DROPBOX_WRITE_ACCOUNT_ID')
        values = {name: env.get(name, '') or '' for name in required}
        missing = [name for name, value in values.items() if not value]
        if len(missing) == len(required):
            return None
        if missing:
            raise DropboxError('Dropbox write credentials incomplete: missing ' + ', '.join(missing))
        app_key = values['DROPBOX_WRITE_APP_KEY']
        refresh_token = values['DROPBOX_WRITE_REFRESH_TOKEN']
        account_id = values['DROPBOX_WRITE_ACCOUNT_ID']
        return cls(app_key=app_key, refresh_token=refresh_token, account_id=account_id,
                   namespace_id=env.get('DROPBOX_WRITE_ROOT_NAMESPACE_ID', ''),
                   root=env.get('DROPBOX_WRITE_ROOT', '') or DEFAULT_ROOT)

    # transport -------------------------------------------------------------

    def _post(self, url, body, headers, *, limit=16 * 1024 * 1024, timeout=TIMEOUT):
        request = urllib.request.Request(url, data=body, headers=headers, method='POST')
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data = response.read(limit + 1)
        except urllib.error.HTTPError as exc:
            raise self._http_error(exc) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise DropboxError('Dropbox transport unavailable or timed out') from None
        if len(data) > limit:
            raise DropboxError('Dropbox response exceeds byte limit')
        return data

    @staticmethod
    def _http_error(exc):
        # The payload is inspected for the conflict tag only and never surfaced.
        if exc.code == 409:
            try:
                summary = json.loads(exc.read(65536)).get('error_summary', '')
            except Exception:
                summary = ''
            if isinstance(summary, str) and summary.startswith('path/conflict/file'):
                return _FileConflict('Dropbox upload conflict', status=409)
        retry_after = None
        if exc.code == 429:
            try:
                retry_after = max(1, min(86400, int(exc.headers.get('Retry-After', '60'))))
            except (ValueError, TypeError):
                retry_after = 60
        return DropboxError(f'Dropbox HTTP {exc.code}', status=exc.code, retry_after=retry_after)

    def _json(self, data, message):
        try:
            value = json.loads(data)
        except (ValueError, TypeError):
            raise DropboxError(message) from None
        if not isinstance(value, dict):
            raise DropboxError(message)
        return value

    def _refresh(self):
        body = urllib.parse.urlencode({'grant_type': 'refresh_token', 'refresh_token': self.refresh_token,
                                       'client_id': self.app_key}).encode()
        result = self._json(self._post(TOKEN_URL, body, {'Content-Type': 'application/x-www-form-urlencoded'},
                                       limit=65536), 'Invalid Dropbox token response')
        try:
            token = result['access_token']
            expires_in = int(result['expires_in'])
        except (KeyError, ValueError, TypeError):
            raise DropboxError('Invalid Dropbox token response') from None
        if not isinstance(token, str) or not token:
            raise DropboxError('Invalid Dropbox token response')
        self.token = token
        self.expires_at = time.monotonic() + max(1, expires_in - 60)

    def _bearer(self):
        if not self.token or time.monotonic() >= self.expires_at:
            self._refresh()
        return 'Bearer ' + self.token

    def _path_root(self, headers):
        if self.namespace_id:
            headers['Dropbox-API-Path-Root'] = json.dumps({'.tag': 'root', 'root': self.namespace_id})
        return headers

    def _rpc(self, endpoint, args, root=True):
        if endpoint not in RPC_ALLOWLIST:
            raise DropboxError('Dropbox operation not permitted')
        headers = {'Authorization': self._bearer(), 'Content-Type': 'application/json'}
        if root:
            self._path_root(headers)
        return self._json(self._post(RPC_BASE + endpoint, json.dumps(args).encode(), headers),
                          'Invalid Dropbox JSON response')

    # public ----------------------------------------------------------------

    def connect(self):
        self.verified = False
        self._refresh()
        account = self._rpc('users/get_current_account', None, root=False)
        if account.get('account_id') != self.account_id:
            raise DropboxError('Dropbox account differs from authorized account')
        if self.namespace_id:
            root_info = account.get('root_info')
            namespace = root_info.get('root_namespace_id') if isinstance(root_info, dict) else None
            if namespace != self.namespace_id:
                raise DropboxError('Dropbox namespace differs from configured namespace')
        self.verified = True

    def check_remote(self, remote_path):
        if not isinstance(remote_path, str) or not remote_path.startswith(self.root + '/'):
            raise DropboxError('Dropbox path outside write root')
        _check_segments(remote_path, 'Invalid Dropbox path')
        if not remote_path.lower().endswith('.pdf'):
            raise DropboxError('Only PDF uploads permitted')
        return remote_path

    def upload(self, local, remote_path):
        remote_path = self.check_remote(remote_path)
        local = Path(local)
        try:
            if not local.is_file() or local.is_symlink():
                raise DropboxError('Upload source missing or not a regular file')
            size = local.stat().st_size
            if not 0 < size <= MAX_UPLOAD:
                raise DropboxError('Upload source empty or exceeds size limit')
            data = local.read_bytes()
        except OSError:
            raise DropboxError('Upload source unreadable') from None
        if not 0 < len(data) <= MAX_UPLOAD:
            raise DropboxError('Upload source empty or exceeds size limit')
        if not self.verified:
            self.connect()
        expected = content_hash(data)
        arg = {'path': remote_path, 'mode': 'add', 'autorename': False, 'mute': True,
               'strict_conflict': False, 'content_hash': expected}
        headers = self._path_root({'Authorization': self._bearer(), 'Content-Type': 'application/octet-stream',
                                   'Dropbox-API-Arg': json.dumps(arg)})
        try:
            body = self._post(UPLOAD_URL, data, headers, limit=1024 * 1024, timeout=UPLOAD_TIMEOUT)
        except _FileConflict:
            existing = self._rpc('files/get_metadata', {'path': remote_path})
            if existing.get('.tag') != 'file' or existing.get('content_hash') != expected:
                raise DropboxError('Dropbox upload conflict', status=409) from None
            return self._result(existing, remote_path, expected, already_present=True)
        return self._result(self._json(body, 'Invalid Dropbox upload response'), remote_path, expected,
                            already_present=False)

    @staticmethod
    def _result(meta, remote_path, expected, *, already_present):
        if meta.get('content_hash') != expected:
            raise DropboxError('Dropbox content hash mismatch')
        if meta.get('path_lower') != remote_path.lower():
            raise DropboxError('Dropbox path mismatch')
        if not isinstance(meta.get('id'), str) or not isinstance(meta.get('rev'), str):
            raise DropboxError('Invalid Dropbox file metadata')
        return UploadResult(path_lower=meta['path_lower'], id=meta['id'], rev=meta['rev'],
                            content_hash=expected, already_present=already_present)


def _slug(title):
    ascii_title = unicodedata.normalize('NFKD', title or '').encode('ascii', 'ignore').decode()
    slug = re.sub(r'[^a-z0-9]+', '-', ascii_title.lower()).strip('-')
    return slug[:SLUG_MAX].rstrip('-') or 'marketdesk'


def _segment(value, limit=NAME_MAX):
    """One Dropbox path segment from a library name: no separators, colons or control characters,
    no leading/trailing dots or spaces (so never '.' or '..'), whitespace collapsed, bounded."""
    text = re.sub(r'[\x00-\x1f\x7f/\\:]', ' ', str(value or ''))
    text = re.sub(r'\s+', ' ', text).strip(' .')
    return text[:limit].rstrip(' .')


def marketdesk_path(published_at, title, article_id, root=DEFAULT_ROOT, folders=None, suffix_id=False):
    """Dropbox path for one MarketDesk PDF.

    With ``folders`` (the library chain after "Current": year, month, day, bank, desk...) the path
    mirrors marketdesk.ai's own layout and keeps the item name: <root>/2026/October/Oct 26/<bank>/<name>.pdf.
    ``suffix_id`` appends " (<article id>)" to the name for a same-name collision. Without a dated chain
    it falls back to the chronological layout on the America/New_York date (naive datetimes are UTC).
    """
    if not isinstance(article_id, str) or not ARTICLE_ID_RE.match(article_id):
        raise ValueError('invalid MarketDesk article id')
    chain = [_segment(f) for f in folders or []] if isinstance(folders, (list, tuple)) else []
    if len(chain) >= 3 and re.fullmatch(r'\d{4}', chain[0]):
        name = _segment(re.sub(r'(?i)\.pdf$', '', str(title or ''))) or article_id
        if suffix_id:
            name = f'{name[:NAME_MAX - len(article_id) - 3]} ({article_id})'
        return '/'.join([_clean_root(root), *[c for c in chain if c], name + '.pdf'])
    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=timezone.utc)
    day = published_at.astimezone(NEW_YORK).date()
    month = MONTHS[day.month - 1]
    return f'{_clean_root(root)}/{day.year}/{month}/{month[:3]} {day.day:02}/{_slug(title)}-{article_id}.pdf'
