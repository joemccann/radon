"""Read-only Dropbox API with an enforced account, namespace and folder boundary."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

MAX_BYTES = 100 * 1024 * 1024
TIMEOUT = 45

class DropboxError(RuntimeError):
    def __init__(self, message, *, status=None, retry_after=None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


ReaderError = DropboxError


def save_private(path, data):
    path = Path(path)
    if path.is_symlink():
        raise ReaderError('Symlink output rejected')
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.parent.is_symlink() or path.parent.stat().st_mode & 0o077:
        raise ReaderError('Output directory must be private')
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as target:
            target.write(data)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def content_hash(data):
    blocks = b''.join(hashlib.sha256(data[i:i + 4194304]).digest() for i in range(0, len(data), 4194304))
    return hashlib.sha256(blocks).hexdigest()


class DropboxClient:
    def __init__(self, config):
        self.config = dict(config)
        self.root = self.config['folder_path'].rstrip('/').lower()
        if self.root != '/joe mccann/current':
            raise DropboxError('Unexpected folder binding')
        self.binding = {'namespace_id': config['root_namespace_id'],
                        'account_id': config['account_id'], 'folder_id': config['folder_id']}
        self.credentials = {'app_key': config['app_key'], 'refresh_token': config['refresh_token'],
                            'account_id': config['account_id']}
        self.token = None
        self.expires_at = 0
        self.verified = False

    @classmethod
    def from_env(cls, env=None):
        env = os.environ if env is None else env
        names = ('APP_KEY', 'REFRESH_TOKEN', 'ACCOUNT_ID', 'ROOT_NAMESPACE_ID', 'FOLDER_ID', 'FOLDER_PATH')
        if any(not env.get('DROPBOX_' + name) for name in names):
            raise DropboxError('Missing required Dropbox runtime configuration')
        return cls({name.lower(): env['DROPBOX_' + name] for name in names})

    def _refresh(self):
        body = urllib.parse.urlencode({'grant_type': 'refresh_token',
            'refresh_token': self.credentials['refresh_token'], 'client_id': self.credentials['app_key']}).encode()
        data, _ = self._post('https://api.dropboxapi.com/oauth2/token', body,
                            {'Content-Type': 'application/x-www-form-urlencoded'}, 65536)
        try:
            result = json.loads(data)
            self.token = result['access_token']
            self.expires_at = time.monotonic() + max(1, int(result['expires_in']) - 60)
        except (KeyError, ValueError, TypeError):
            raise DropboxError('Invalid Dropbox token response') from None

    def path(self, relative=''):
        if not isinstance(relative, str) or relative.startswith('/') or ':' in relative or '\\' in relative:
            raise ReaderError('Only relative folder paths accepted')
        parts = relative.split('/')
        if any(p in ('.', '..') for p in parts) or any(ord(c) < 32 for c in relative):
            raise ReaderError('Invalid path segment')
        return self.check(self.root + ('/' + relative if relative else ''))

    def check(self, path):
        # Absolute Dropbox metadata paths may contain filename colons. The leading
        # slash and root boundary exclude id:/ns:/rev: selectors; downloads use
        # hashed local names, never the provider filename as a filesystem path.
        if not isinstance(path, str) or not path.startswith('/') or '\\' in path:
            raise ReaderError('Invalid Dropbox path')
        if any(p in ('.', '..', '') for p in path.split('/')[1:]) or any(ord(c) < 32 for c in path):
            raise ReaderError('Invalid Dropbox path segment')
        lower = path.lower()
        if lower != self.root and not lower.startswith(self.root + '/'):
            raise ReaderError('Dropbox path outside watched folder')
        return path

    def _post(self, url, body, headers, limit=MAX_BYTES):
        request = urllib.request.Request(url, data=body, headers=headers, method='POST')
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                data = response.read(limit + 1)
                if len(data) > limit:
                    raise ReaderError('Response exceeds byte limit')
                return data, response.headers
        except urllib.error.HTTPError as exc:
            # Do not disclose request headers, tokens, or Dropbox error payloads.
            try:
                retry_after = max(1, min(86400, int(exc.headers.get('Retry-After', '60')))) if exc.code == 429 else None
            except (ValueError, TypeError):
                retry_after = 60
            raise DropboxError(f'Dropbox HTTP {exc.code}', status=exc.code, retry_after=retry_after) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise ReaderError('Dropbox transport unavailable or timed out') from None

    def _rpc(self, endpoint, args, root=True):
        allowed = {'users/get_current_account', 'files/get_metadata', 'files/list_folder', 'files/list_folder/continue'}
        if endpoint not in allowed:
            raise ReaderError('Dropbox operation not permitted')
        if not self.token or time.monotonic() >= self.expires_at:
            self._refresh()
        headers = {'Authorization': 'Bearer ' + self.token, 'Content-Type': 'application/json'}
        if root:
            headers['Dropbox-API-Path-Root'] = json.dumps({'.tag': 'root', 'root': self.binding['namespace_id']})
        data, _ = self._post('https://api.dropboxapi.com/2/' + endpoint, json.dumps(args).encode(), headers, 16 * 1024 * 1024)
        try:
            return json.loads(data)
        except (ValueError, TypeError):
            raise DropboxError('Invalid Dropbox JSON response') from None

    def connect(self):
        self.verified = False
        self._refresh()
        account = self._rpc('users/get_current_account', None, root=False)
        if account.get('account_id') != self.binding['account_id'] or account.get('account_id') != self.credentials['account_id'] or account.get('email', '').lower() != 'joe@asymmetric.financial':
            raise ReaderError('Dropbox account differs from authorized account')
        if account.get('root_info', {}).get('root_namespace_id') != self.binding['namespace_id']:
            raise ReaderError('Dropbox namespace differs from folder binding')
        metadata = self._rpc('files/get_metadata', {'path': self.root})
        self.validate(metadata)
        if metadata.get('id') != self.binding['folder_id'] or metadata.get('.tag') != 'folder' or metadata.get('path_lower') != self.root:
            raise ReaderError('Watched folder binding changed')
        self.verified = True
        return self

    def validate(self, metadata):
        self.check(metadata.get('path_lower'))
        self.check(metadata.get('path_display'))
        if metadata['path_lower'] != metadata['path_display'].lower():
            raise ReaderError('Inconsistent Dropbox metadata path')
        if metadata.get('.tag') not in ('file', 'folder', 'deleted'):
            raise ReaderError('Unexpected Dropbox metadata type')
        return metadata

    def ready(self):
        if not self.verified:
            raise ReaderError('Account and folder must be verified first')

    def metadata(self, relative=''):
        self.ready()
        path = self.path(relative)
        item = self.validate(self._rpc('files/get_metadata', {'path': path}))
        if item['path_lower'] != path.lower():
            raise ReaderError('Metadata path mismatch')
        return item

    def list_page(self, relative='', cursor=None):
        self.ready()
        path = self.path(relative)
        if cursor is None:
            page = self._rpc('files/list_folder', {'path': path, 'recursive': True,
                'include_deleted': True, 'limit': 2000})
        else:
            if not isinstance(cursor, str) or not cursor:
                raise DropboxError('Invalid cursor')
            page = self._rpc('files/list_folder/continue', {'cursor': cursor})
        if not isinstance(page.get('cursor'), str) or not page['cursor'] or not isinstance(page.get('has_more'), bool) or not isinstance(page.get('entries'), list):
            raise DropboxError('Invalid listing response')
        entries = []
        for item in page['entries']:
            self.validate(item)
            if item['path_lower'] == path.lower() and item['.tag'] == 'folder':
                continue
            if not item['path_lower'].startswith(path.lower() + '/'):
                raise DropboxError('Listing escaped requested folder')
            entries.append(item)
        return {**page, 'entries': entries}

    def download(self, entry, output_dir):
        self.ready()
        self.validate(entry)
        if entry.get('.tag') != 'file' or not isinstance(entry.get('size'), int) or not 0 <= entry['size'] <= MAX_BYTES:
            raise ReaderError('Invalid file or size limit exceeded')
        if not entry.get('rev') or not entry.get('content_hash'):
            raise ReaderError('Revision and content hash required')
        if not self.token or time.monotonic() >= self.expires_at:
            self._refresh()
        headers = {'Authorization': 'Bearer ' + self.token, 'Dropbox-API-Path-Root': json.dumps({'.tag': 'root', 'root': self.binding['namespace_id']}), 'Dropbox-API-Arg': json.dumps({'path': entry['path_lower'], 'rev': entry['rev']})}
        data, response_headers = self._post('https://content.dropboxapi.com/2/files/download', b'', headers)
        received = json.loads(response_headers['Dropbox-API-Result'])
        # download returns FileMetadata directly, without the union .tag.
        received.setdefault('.tag', 'file')
        self.validate(received)
        for field in ('id', 'path_lower', 'rev', 'content_hash', 'size'):
            if received.get(field) != entry.get(field):
                raise ReaderError('Downloaded metadata mismatch: ' + field)
        if len(data) != entry['size'] or content_hash(data) != entry['content_hash']:
            raise ReaderError('Downloaded content hash or size mismatch')
        name = hashlib.sha256((entry['path_lower'] + '\0' + entry['rev']).encode()).hexdigest() + '.pdf'
        target = Path(output_dir) / name
        save_private(target, data)
        return target
