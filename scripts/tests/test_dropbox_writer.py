"""Write-scoped Dropbox client for MarketDesk PDFs (research.dropbox_writer).

Tests assert at the wire: urllib.request.urlopen is replaced by a fake that
records every Request (full URL, method, headers, body) and returns scripted
responses, so a wrong endpoint, header or Dropbox-API-Arg fails here.
"""
import io
import json
import sys
import tempfile
import time
import unittest
import urllib.error
from datetime import datetime, timezone
from email.message import Message
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from research import dropbox_writer as dw  # noqa: E402
from research.dropbox import DropboxError, content_hash  # noqa: E402

TOKEN_URL = 'https://api.dropboxapi.com/oauth2/token'
ACCOUNT_URL = 'https://api.dropboxapi.com/2/users/get_current_account'
METADATA_URL = 'https://api.dropboxapi.com/2/files/get_metadata'
SECRET_REFRESH = 'refresh-SECRET-abc'
SECRET_ACCESS = 'access-SECRET-xyz'
REMOTE = '/Radon/MarketDesk/2026/October/Oct 02/fed-cuts-4AJs5Adl2Yp.pdf'
ENV = {
    'DROPBOX_WRITE_APP_KEY': 'appkey',
    'DROPBOX_WRITE_REFRESH_TOKEN': SECRET_REFRESH,
    'DROPBOX_WRITE_ACCOUNT_ID': 'dbid:owner',
}


class FakeResponse:
    def __init__(self, body, headers=None):
        self._body = io.BytesIO(body)
        self.headers = Message()
        for key, value in (headers or {}).items():
            self.headers[key] = value

    def read(self, limit=-1):
        return self._body.read(limit)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def http_error(url, code, body=b'', headers=None):
    msg = Message()
    for key, value in (headers or {}).items():
        msg[key] = value
    return urllib.error.HTTPError(url, code, 'err', msg, io.BytesIO(body))


class Wire:
    """Scripted urlopen: each call pops the next response (or raises it)."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, request, timeout=None):
        self.requests.append(request)
        if not self.responses:
            raise AssertionError('unexpected request to ' + request.full_url)
        nxt = self.responses.pop(0)
        if isinstance(nxt, BaseException):
            raise nxt
        return nxt

    def header(self, index, name):
        return {k.lower(): v for k, v in self.requests[index].header_items()}.get(name.lower())


def token_response(expires_in=14400):
    return FakeResponse(json.dumps({'access_token': SECRET_ACCESS, 'expires_in': expires_in,
                                    'token_type': 'bearer'}).encode())


def account_response(account_id='dbid:owner', namespace='910135760'):
    return FakeResponse(json.dumps({'account_id': account_id, 'email': 'x@example.com',
                                    'root_info': {'.tag': 'user', 'root_namespace_id': namespace,
                                                  'home_namespace_id': namespace}}).encode())


def file_metadata(data, path=REMOTE):
    return {'.tag': 'file', 'name': path.rsplit('/', 1)[1], 'path_lower': path.lower(),
            'path_display': path, 'id': 'id:abc', 'rev': '015f00', 'size': len(data),
            'content_hash': content_hash(data)}


class FromEnvTests(unittest.TestCase):
    def test_none_only_when_every_required_key_is_absent(self):
        self.assertIsNone(dw.DropboxWriter.from_env({}))
        self.assertIsNone(dw.DropboxWriter.from_env({name: '' for name in ENV}))

    def test_incomplete_credentials_raise_naming_only_the_missing_keys(self):
        for name in ENV:
            for value in (None, ''):
                env = dict(ENV)
                if value is None:
                    del env[name]
                else:
                    env[name] = value
                with self.subTest(name=name, value=value):
                    with self.assertRaises(dw.DropboxError) as info:
                        dw.DropboxWriter.from_env(env)
                    message = str(info.exception)
                    self.assertIn('incomplete', message)
                    self.assertIn(name, message)
                    for other, secret in ENV.items():
                        if other != name and secret:
                            self.assertNotIn(secret, message)

    def test_reads_required_and_optional_values(self):
        writer = dw.DropboxWriter.from_env(dict(ENV))
        self.assertIsInstance(writer, dw.DropboxWriter)
        self.assertEqual(writer.root, dw.DEFAULT_ROOT)
        self.assertIsNone(writer.namespace_id)
        writer = dw.DropboxWriter.from_env({**ENV, 'DROPBOX_WRITE_ROOT_NAMESPACE_ID': '910135760',
                                            'DROPBOX_WRITE_ROOT': '/Radon/Other/'})
        self.assertEqual(writer.namespace_id, '910135760')
        self.assertEqual(writer.root, '/Radon/Other')

    def test_does_not_read_the_read_only_reader_credentials(self):
        env = {'DROPBOX_APP_KEY': 'k', 'DROPBOX_REFRESH_TOKEN': 'r', 'DROPBOX_ACCOUNT_ID': 'a'}
        self.assertIsNone(dw.DropboxWriter.from_env(env))

    def test_rejects_unsafe_root(self):
        for root in ('Radon/MarketDesk', '/Radon/../etc', '/', 'id:abc', '/Radon\\x'):
            with self.subTest(root=root), self.assertRaises(DropboxError):
                dw.DropboxWriter.from_env({**ENV, 'DROPBOX_WRITE_ROOT': root})

    def test_constants(self):
        self.assertEqual(dw.DEFAULT_ROOT, '/Radon/MarketDesk')
        self.assertEqual(dw.UPLOAD_URL, 'https://content.dropboxapi.com/2/files/upload')
        self.assertEqual(dw.MAX_UPLOAD, 100 * 1024 * 1024)


class ConnectTests(unittest.TestCase):
    def test_secret_less_refresh_then_identity_check(self):
        writer = dw.DropboxWriter.from_env({**ENV, 'DROPBOX_WRITE_ROOT_NAMESPACE_ID': '910135760'})
        wire = Wire(token_response(), account_response())
        with patch.object(dw.urllib.request, 'urlopen', wire):
            writer.connect()
        self.assertTrue(writer.verified)
        refresh, account = wire.requests
        self.assertEqual(refresh.full_url, TOKEN_URL)
        self.assertEqual(refresh.get_method(), 'POST')
        form = dict(p.split('=', 1) for p in refresh.data.decode().split('&'))
        self.assertEqual(form, {'grant_type': 'refresh_token', 'refresh_token': SECRET_REFRESH,
                                'client_id': 'appkey'})
        self.assertNotIn('client_secret', refresh.data.decode())
        self.assertIsNone(wire.header(0, 'Authorization'))
        self.assertEqual(account.full_url, ACCOUNT_URL)
        self.assertEqual(wire.header(1, 'Authorization'), 'Bearer ' + SECRET_ACCESS)
        self.assertEqual(account.data, b'null')
        self.assertIsNone(wire.header(1, 'Dropbox-API-Path-Root'))

    def test_account_mismatch_refused(self):
        writer = dw.DropboxWriter.from_env(dict(ENV))
        wire = Wire(token_response(), account_response(account_id='dbid:someone-else'))
        with patch.object(dw.urllib.request, 'urlopen', wire), self.assertRaises(DropboxError) as ctx:
            writer.connect()
        self.assertFalse(writer.verified)
        self.assertIn('account', str(ctx.exception).lower())

    def test_namespace_mismatch_refused_only_when_configured(self):
        writer = dw.DropboxWriter.from_env({**ENV, 'DROPBOX_WRITE_ROOT_NAMESPACE_ID': '910135760'})
        wire = Wire(token_response(), account_response(namespace='123'))
        with patch.object(dw.urllib.request, 'urlopen', wire), self.assertRaises(DropboxError) as ctx:
            writer.connect()
        self.assertIn('namespace', str(ctx.exception).lower())
        writer = dw.DropboxWriter.from_env(dict(ENV))
        with patch.object(dw.urllib.request, 'urlopen', Wire(token_response(), account_response(namespace='123'))):
            writer.connect()
        self.assertTrue(writer.verified)

    def test_invalid_token_response(self):
        writer = dw.DropboxWriter.from_env(dict(ENV))
        with patch.object(dw.urllib.request, 'urlopen', Wire(FakeResponse(b'{"nope":1}'))), \
                self.assertRaises(DropboxError):
            writer.connect()

    def test_refresh_http_error_is_redacted(self):
        writer = dw.DropboxWriter.from_env(dict(ENV))
        err = http_error(TOKEN_URL, 400, json.dumps({'error': 'invalid_grant',
                                                     'echo': SECRET_REFRESH}).encode())
        with patch.object(dw.urllib.request, 'urlopen', Wire(err)), self.assertRaises(DropboxError) as ctx:
            writer.connect()
        self.assertEqual(ctx.exception.status, 400)
        self.assertNotIn(SECRET_REFRESH, str(ctx.exception))
        self.assertNotIn('invalid_grant', str(ctx.exception))
        self.assertIsNone(ctx.exception.__cause__)
        self.assertTrue(ctx.exception.__suppress_context__)


class RpcAllowlistTests(unittest.TestCase):
    def test_non_allowlisted_rpc_refused_without_network(self):
        writer = dw.DropboxWriter.from_env(dict(ENV))
        wire = Wire()
        with patch.object(dw.urllib.request, 'urlopen', wire):
            for endpoint in ('files/delete_v2', 'files/list_folder', 'files/move_v2', 'files/upload',
                             'sharing/create_shared_link_with_settings', '../files/delete_v2'):
                with self.subTest(endpoint=endpoint), self.assertRaises(DropboxError):
                    writer._rpc(endpoint, {'path': REMOTE})
        self.assertEqual(wire.requests, [])


class UploadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = b'%PDF-1.4 marketdesk sample' * 10
        self.local = Path(self.tmp.name) / 'a.pdf'
        self.local.write_bytes(self.data)
        self.writer = dw.DropboxWriter.from_env({**ENV, 'DROPBOX_WRITE_ROOT_NAMESPACE_ID': '910135760'})
        self.writer.verified = True
        self.writer.token = SECRET_ACCESS
        self.writer.expires_at = time.monotonic() + 3600

    def tearDown(self):
        self.tmp.cleanup()

    def test_exact_upload_request(self):
        wire = Wire(FakeResponse(json.dumps(file_metadata(self.data)).encode()))
        with patch.object(dw.urllib.request, 'urlopen', wire):
            result = self.writer.upload(self.local, REMOTE)
        self.assertEqual(result, dw.UploadResult(path_lower=REMOTE.lower(), id='id:abc', rev='015f00',
                                                 content_hash=content_hash(self.data), already_present=False))
        (req,) = wire.requests
        self.assertEqual(req.full_url, 'https://content.dropboxapi.com/2/files/upload')
        self.assertEqual(req.get_method(), 'POST')
        self.assertEqual(req.data, self.data)
        self.assertEqual(wire.header(0, 'Authorization'), 'Bearer ' + SECRET_ACCESS)
        self.assertEqual(wire.header(0, 'Content-Type'), 'application/octet-stream')
        self.assertEqual(json.loads(wire.header(0, 'Dropbox-API-Path-Root')),
                         {'.tag': 'root', 'root': '910135760'})
        self.assertEqual(json.loads(wire.header(0, 'Dropbox-API-Arg')), {
            'path': REMOTE, 'mode': 'add', 'autorename': False, 'mute': True,
            'strict_conflict': False, 'content_hash': content_hash(self.data)})

    def test_api_arg_header_is_ascii_safe(self):
        path = '/Radon/MarketDesk/2026/October/Oct 02/café-4AJs5Adl2Yp.pdf'
        wire = Wire(FakeResponse(json.dumps(file_metadata(self.data, path)).encode()))
        with patch.object(dw.urllib.request, 'urlopen', wire):
            self.writer.upload(self.local, path)
        raw = wire.header(0, 'Dropbox-API-Arg')
        raw.encode('ascii')
        self.assertEqual(json.loads(raw)['path'], path)

    def test_no_path_root_header_without_namespace(self):
        writer = dw.DropboxWriter.from_env(dict(ENV))
        writer.verified, writer.token, writer.expires_at = True, SECRET_ACCESS, time.monotonic() + 3600
        wire = Wire(FakeResponse(json.dumps(file_metadata(self.data)).encode()))
        with patch.object(dw.urllib.request, 'urlopen', wire):
            writer.upload(self.local, REMOTE)
        self.assertIsNone(wire.header(0, 'Dropbox-API-Path-Root'))

    def test_upload_connects_first_when_unverified(self):
        self.writer.verified = False
        self.writer.token = None
        wire = Wire(token_response(), account_response(),
                    FakeResponse(json.dumps(file_metadata(self.data)).encode()))
        with patch.object(dw.urllib.request, 'urlopen', wire):
            self.writer.upload(self.local, REMOTE)
        self.assertEqual([r.full_url for r in wire.requests], [TOKEN_URL, ACCOUNT_URL, dw.UPLOAD_URL])

    def test_expired_token_refreshes(self):
        self.writer.expires_at = time.monotonic() - 1
        wire = Wire(token_response(), FakeResponse(json.dumps(file_metadata(self.data)).encode()))
        with patch.object(dw.urllib.request, 'urlopen', wire):
            self.writer.upload(self.local, REMOTE)
        self.assertEqual([r.full_url for r in wire.requests], [TOKEN_URL, dw.UPLOAD_URL])

    def test_response_hash_mismatch_raises(self):
        meta = file_metadata(b'something else')
        wire = Wire(FakeResponse(json.dumps(meta).encode()))
        with patch.object(dw.urllib.request, 'urlopen', wire), self.assertRaises(DropboxError):
            self.writer.upload(self.local, REMOTE)

    def test_response_path_mismatch_raises(self):
        meta = file_metadata(self.data, '/Radon/MarketDesk/2026/October/Oct 02/fed-cuts-4AJs5Adl2Yp (1).pdf')
        with patch.object(dw.urllib.request, 'urlopen', Wire(FakeResponse(json.dumps(meta).encode()))), \
                self.assertRaises(DropboxError):
            self.writer.upload(self.local, REMOTE)

    def test_invalid_json_response_raises(self):
        with patch.object(dw.urllib.request, 'urlopen', Wire(FakeResponse(b'<html>'))), \
                self.assertRaises(DropboxError):
            self.writer.upload(self.local, REMOTE)

    def conflict(self):
        body = json.dumps({'error_summary': 'path/conflict/file/..',
                           'error': {'.tag': 'path', 'reason': {'.tag': 'conflict', 'conflict': {'.tag': 'file'}}}})
        return http_error(dw.UPLOAD_URL, 409, body.encode())

    def test_409_conflict_with_equal_hash_is_already_present(self):
        wire = Wire(self.conflict(), FakeResponse(json.dumps(file_metadata(self.data)).encode()))
        with patch.object(dw.urllib.request, 'urlopen', wire):
            result = self.writer.upload(self.local, REMOTE)
        self.assertTrue(result.already_present)
        self.assertEqual(result.content_hash, content_hash(self.data))
        self.assertEqual(result.path_lower, REMOTE.lower())
        meta_req = wire.requests[1]
        self.assertEqual(meta_req.full_url, METADATA_URL)
        self.assertEqual(json.loads(meta_req.data), {'path': REMOTE})
        self.assertEqual(json.loads(wire.header(1, 'Dropbox-API-Path-Root')),
                         {'.tag': 'root', 'root': '910135760'})

    def test_409_conflict_with_different_hash_raises_conflict(self):
        wire = Wire(self.conflict(), FakeResponse(json.dumps(file_metadata(b'other bytes')).encode()))
        with patch.object(dw.urllib.request, 'urlopen', wire), self.assertRaises(DropboxError) as ctx:
            self.writer.upload(self.local, REMOTE)
        self.assertEqual(ctx.exception.status, 409)
        self.assertIn('conflict', str(ctx.exception).lower())

    def test_409_conflict_with_folder_at_path_raises(self):
        folder = {'.tag': 'folder', 'name': 'x', 'path_lower': REMOTE.lower(), 'path_display': REMOTE, 'id': 'id:f'}
        wire = Wire(self.conflict(), FakeResponse(json.dumps(folder).encode()))
        with patch.object(dw.urllib.request, 'urlopen', wire), self.assertRaises(DropboxError):
            self.writer.upload(self.local, REMOTE)

    def test_other_409_is_not_treated_as_conflict(self):
        body = json.dumps({'error_summary': 'path/insufficient_space/..', 'secret': SECRET_ACCESS})
        wire = Wire(http_error(dw.UPLOAD_URL, 409, body.encode()))
        with patch.object(dw.urllib.request, 'urlopen', wire), self.assertRaises(DropboxError) as ctx:
            self.writer.upload(self.local, REMOTE)
        self.assertEqual(len(wire.requests), 1)
        self.assertEqual(ctx.exception.status, 409)
        self.assertNotIn(SECRET_ACCESS, str(ctx.exception))
        self.assertNotIn('insufficient_space', str(ctx.exception))

    def test_429_carries_retry_after(self):
        wire = Wire(http_error(dw.UPLOAD_URL, 429, b'{}', {'Retry-After': '30'}))
        with patch.object(dw.urllib.request, 'urlopen', wire), self.assertRaises(DropboxError) as ctx:
            self.writer.upload(self.local, REMOTE)
        self.assertEqual((ctx.exception.status, ctx.exception.retry_after), (429, 30))

    def test_errors_are_redacted(self):
        body = json.dumps({'error_summary': 'invalid_access_token', 'token': SECRET_ACCESS}).encode()
        for exc in (http_error(dw.UPLOAD_URL, 401, body), urllib.error.URLError('dns ' + SECRET_ACCESS),
                    TimeoutError('slow')):
            wire = Wire(exc)
            with self.subTest(exc=type(exc).__name__), patch.object(dw.urllib.request, 'urlopen', wire), \
                    self.assertRaises(DropboxError) as ctx:
                self.writer.upload(self.local, REMOTE)
            message = str(ctx.exception)
            self.assertNotIn(SECRET_ACCESS, message)
            self.assertNotIn(SECRET_REFRESH, message)
            self.assertNotIn('invalid_access_token', message)
            self.assertIsNone(ctx.exception.__cause__)

    def test_remote_path_must_stay_under_root(self):
        wire = Wire()
        bad = ['/Elsewhere/a.pdf', '/Radon/MarketDeskish/a.pdf', '/Radon/MarketDesk/../x.pdf',
               '/Radon/MarketDesk//a.pdf', 'id:abc', 'Radon/MarketDesk/a.pdf', '/Radon/MarketDesk/a\\b.pdf',
               '/Radon/MarketDesk/a\nb.pdf', '/Radon/MarketDesk/a.exe', '/Radon/MarketDesk']
        with patch.object(dw.urllib.request, 'urlopen', wire):
            for path in bad:
                with self.subTest(path=path), self.assertRaises(DropboxError):
                    self.writer.upload(self.local, path)
        self.assertEqual(wire.requests, [])

    def test_size_cap_and_missing_file(self):
        with patch.object(dw, 'MAX_UPLOAD', 10), patch.object(dw.urllib.request, 'urlopen', Wire()), \
                self.assertRaises(DropboxError):
            self.writer.upload(self.local, REMOTE)
        with patch.object(dw.urllib.request, 'urlopen', Wire()), self.assertRaises(DropboxError):
            self.writer.upload(Path(self.tmp.name) / 'missing.pdf', REMOTE)
        empty = Path(self.tmp.name) / 'empty.pdf'
        empty.write_bytes(b'')
        with patch.object(dw.urllib.request, 'urlopen', Wire()), self.assertRaises(DropboxError):
            self.writer.upload(empty, REMOTE)


class MarketdeskPathTests(unittest.TestCase):
    def test_example_layout(self):
        when = datetime(2026, 10, 2, 14, 0, tzinfo=timezone.utc)
        self.assertEqual(dw.marketdesk_path(when, 'Fed cuts and curve', '4AJs5Adl2Yp'),
                         '/Radon/MarketDesk/2026/October/Oct 02/fed-cuts-and-curve-4AJs5Adl2Yp.pdf')

    def test_new_york_date_rollover(self):
        # 23:30 ET on Oct 1 is 03:30 UTC on Oct 2: folder follows New York.
        when = datetime(2026, 10, 2, 3, 30, tzinfo=timezone.utc)
        self.assertIn('/2026/October/Oct 01/', dw.marketdesk_path(when, 't', '4AJs5Adl2Yp'))
        # Year rollover too.
        when = datetime(2027, 1, 1, 2, 0, tzinfo=timezone.utc)
        self.assertTrue(dw.marketdesk_path(when, 't', '4AJs5Adl2Yp')
                        .startswith('/Radon/MarketDesk/2026/December/Dec 31/'))

    def test_naive_datetime_is_utc(self):
        self.assertIn('/Oct 01/', dw.marketdesk_path(datetime(2026, 10, 2, 3, 30), 't', '4AJs5Adl2Yp'))

    def test_zero_padded_day_and_sep_not_sept(self):
        when = datetime(2026, 9, 7, 16, 0, tzinfo=timezone.utc)
        self.assertIn('/2026/September/Sep 07/', dw.marketdesk_path(when, 't', '4AJs5Adl2Yp'))

    def test_month_names_match_reader_layout(self):
        from research.state import MONTHS
        self.assertIs(dw.MONTHS, MONTHS)

    def test_slug_ascii_kebab_bounded(self):
        when = datetime(2026, 10, 2, 14, 0, tzinfo=timezone.utc)
        title = 'Café — "Rates" & FX:  the 10Y / 2Y spread?! ' + 'x' * 200
        path = dw.marketdesk_path(when, title, '4AJs5Adl2Yp')
        name = path.rsplit('/', 1)[1]
        slug = name[:-len('-4AJs5Adl2Yp.pdf')]
        self.assertTrue(slug.startswith('cafe-rates-fx-the-10y-2y-spread-x'))
        self.assertLessEqual(len(slug), 60)
        self.assertRegex(slug, r'^[a-z0-9]+(?:-[a-z0-9]+)*$')
        path.encode('ascii')

    def test_slug_fallback_when_title_has_no_ascii(self):
        when = datetime(2026, 10, 2, 14, 0, tzinfo=timezone.utc)
        self.assertTrue(dw.marketdesk_path(when, '——', '4AJs5Adl2Yp').endswith('/marketdesk-4AJs5Adl2Yp.pdf'))

    def test_slug_truncation_does_not_end_in_hyphen(self):
        when = datetime(2026, 10, 2, 14, 0, tzinfo=timezone.utc)
        title = 'a' * 59 + ' bcdef'
        name = dw.marketdesk_path(when, title, '4AJs5Adl2Yp').rsplit('/', 1)[1]
        self.assertEqual(name, 'a' * 59 + '-4AJs5Adl2Yp.pdf')

    def test_custom_root_and_bad_ids(self):
        when = datetime(2026, 10, 2, 14, 0, tzinfo=timezone.utc)
        self.assertTrue(dw.marketdesk_path(when, 't', '4AJs5Adl2Yp', root='/Radon/X/')
                        .startswith('/Radon/X/2026/October/Oct 02/'))
        for article_id in ('', 'short', '../../etc', 'a' * 17, 'abc/defghij', 'abcdefgh.pdf'):
            with self.subTest(article_id=article_id), self.assertRaises(ValueError):
                dw.marketdesk_path(when, 't', article_id)

    def test_path_round_trips_through_upload_guard(self):
        when = datetime(2026, 10, 2, 14, 0, tzinfo=timezone.utc)
        writer = dw.DropboxWriter.from_env(dict(ENV))
        path = dw.marketdesk_path(when, 'Fed cuts', '4AJs5Adl2Yp', root=writer.root)
        self.assertEqual(writer.check_remote(path), path)


class MirroredMarketdeskPathTests(unittest.TestCase):
    """With the library folder chain, the path mirrors marketdesk.ai's own layout under the root."""

    WHEN = datetime(2026, 10, 26, 17, 0, tzinfo=timezone.utc)

    def test_mirrors_the_library_chain_and_keeps_the_item_name(self):
        path = dw.marketdesk_path(self.WHEN, 'GS Molavi Ruminations Macro, Micro, Markets 26 Oct 2026', 'eQJttFDd58v',
                                  folders=['2026', 'October', 'Oct 26', 'Goldman Sachs', 'S&T'])
        self.assertEqual(path, '/Radon/MarketDesk/2026/October/Oct 26/Goldman Sachs/S&T/'
                               'GS Molavi Ruminations Macro, Micro, Markets 26 Oct 2026.pdf')

    def test_unsafe_characters_are_neutralised(self):
        path = dw.marketdesk_path(self.WHEN, 'A/B: C\\D\x07 ..', 'eQJttFDd58v',
                                  folders=['2026', 'October', 'Oct 26', '../evil', ' . '])
        self.assertEqual(path, '/Radon/MarketDesk/2026/October/Oct 26/evil/A B C D.pdf')
        writer = dw.DropboxWriter.from_env(dict(ENV))
        self.assertEqual(writer.check_remote(path), path)

    def test_name_already_ending_in_pdf_is_not_doubled_and_long_names_are_bounded(self):
        path = dw.marketdesk_path(self.WHEN, 'Macro_Volatility_Digest_Oct26.pdf', '4AJs5Adl2Yp',
                                  folders=['2026', 'October', 'Oct 26', 'CBOE'])
        self.assertTrue(path.endswith('/CBOE/Macro_Volatility_Digest_Oct26.pdf'))
        name = dw.marketdesk_path(self.WHEN, 'x' * 400, '4AJs5Adl2Yp',
                                  folders=['2026', 'October', 'Oct 26']).rsplit('/', 1)[1]
        self.assertLessEqual(len(name), dw.NAME_MAX + len('.pdf'))

    def test_empty_name_falls_back_to_article_id(self):
        path = dw.marketdesk_path(self.WHEN, ' / ', '4AJs5Adl2Yp', folders=['2026', 'October', 'Oct 26'])
        self.assertTrue(path.endswith('/Oct 26/4AJs5Adl2Yp.pdf'))

    def test_suffix_disambiguates_a_name_collision(self):
        path = dw.marketdesk_path(self.WHEN, 'Tesla Oct 26', 'vL1kqHS98R8',
                                  folders=['2026', 'October', 'Oct 26', 'Deutsche Bank'], suffix_id=True)
        self.assertTrue(path.endswith('/Deutsche Bank/Tesla Oct 26 (vL1kqHS98R8).pdf'))

    def test_chain_without_a_date_falls_back_to_the_chronological_layout(self):
        for folders in (None, [], ['Goldman Sachs'], ['26', 'October', 'Oct 26']):
            with self.subTest(folders=folders):
                self.assertEqual(dw.marketdesk_path(self.WHEN, 'Fed cuts', '4AJs5Adl2Yp', folders=folders),
                                 '/Radon/MarketDesk/2026/October/Oct 26/fed-cuts-4AJs5Adl2Yp.pdf')


if __name__ == '__main__':
    unittest.main()
