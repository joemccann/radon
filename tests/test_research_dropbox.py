import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
from scripts.research.dropbox import DropboxClient, DropboxError as ReaderError, content_hash, save_private
import time
import urllib.error
from unittest.mock import patch

ROOT = '/joe mccann/current'
def item(path=ROOT + '/2026/a.pdf', data=b'pdf'):
    return {'.tag':'file', 'path_lower':path, 'path_display':path, 'name':'a.pdf', 'size':len(data), 'id':'file-id', 'rev':'r1', 'content_hash':content_hash(data)}

class ReaderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.r = DropboxClient({'folder_path':ROOT,'app_key':'fake','refresh_token':'fake','account_id':'bound','root_namespace_id':'namespace','folder_id':'folder'})
        self.r.verified = True
        self.r.token = 'fake'
        self.r.expires_at = time.monotonic()+3600
    def tearDown(self): self.tmp.cleanup()
    def test_boundary(self):
        for path in [ROOT+'ish/a.pdf','/elsewhere/a.pdf',ROOT+'/../a.pdf',ROOT+'//a.pdf','id:foo',ROOT+'/a\\b']:
            with self.subTest(path=path), self.assertRaises(ReaderError): self.r.check(path)
        self.assertEqual(self.r.path('2026/September/Sep 07'), ROOT+'/2026/September/Sep 07')
    def test_traversal(self):
        for path in ['../Currentish','/absolute','id:foo','https://a','foo/../bar','foo\\bar','foo\nbar']:
            with self.subTest(path=path), self.assertRaises(ReaderError): self.r.path(path)
    def test_pagination(self):
        self.r._rpc = Mock(side_effect=[{'entries':[], 'has_more':True, 'cursor':'opaque'}, {'entries':[item()], 'has_more':False,'cursor':'next'}])
        first=self.r.list_page('2026')
        second=self.r.list_page('2026',first['cursor'])
        self.assertEqual(len(second['entries']),1)
        self.assertEqual(self.r._rpc.call_args.args,('files/list_folder/continue',{'cursor':'opaque'}))
    def test_recursive_folder_self(self):
        folder={'.tag':'folder','path_lower':ROOT+'/2026','path_display':ROOT+'/2026'}
        self.r._rpc=Mock(return_value={'entries':[folder,item()], 'has_more':False,'cursor':'c'})
        self.assertEqual(len(self.r.list_page('2026')['entries']),1)
    def test_escaped_pagination(self):
        self.r._rpc=Mock(return_value={'entries':[item(ROOT+'/other/a.pdf')],'has_more':False,'cursor':'c'})
        with self.assertRaises(ReaderError):self.r.list_page('2026','opaque')
    def test_nested_and_deleted(self):
        deleted={'.tag':'deleted','path_lower':ROOT+'/2026/old','path_display':ROOT+'/2026/old'}
        self.r._rpc=Mock(return_value={'entries':[item(ROOT+'/2026/nested/a.pdf'),deleted],'has_more':False,'cursor':'c'})
        self.assertEqual(len(self.r.list_page('2026')['entries']),2)
    def test_colon_filename_lists_and_downloads_without_local_path_interpretation(self):
        path = ROOT + '/2026/deutsche bank/research chartbook: the home straight....pdf'
        entry = item(path)
        self.r._rpc = Mock(return_value={'entries': [entry], 'has_more': False, 'cursor': 'next'})
        self.assertEqual(self.r.list_page('2026', 'previous')['entries'], [entry])
        received = dict(entry)
        received.pop('.tag')
        self.r._post = Mock(return_value=(b'pdf', {'Dropbox-API-Result': json.dumps(received)}))
        target = self.r.download(entry, self.base / 'downloads')
        self.assertEqual(target.read_bytes(), b'pdf')
        self.assertRegex(target.name, r'^[a-f0-9]{64}\.pdf$')
        download_args = json.loads(self.r._post.call_args.args[2]['Dropbox-API-Arg'])
        self.assertEqual(download_args, {'path': path, 'rev': 'r1'})

    def test_colon_does_not_weaken_remote_path_boundary(self):
        for path in ['id:foo', 'ns:123/a.pdf', 'rev:123', ROOT + 'ish/a:b.pdf',
                     ROOT + '/../a:b.pdf', ROOT + '/a:b\\c.pdf', ROOT + '/a:b\n.pdf']:
            with self.subTest(path=path), self.assertRaises(ReaderError):
                self.r.check(path)
        with self.assertRaises(ReaderError):
            self.r.path('2026/id:foo')
        entry = item(ROOT + '/2026/a:b.pdf')
        entry['path_display'] = ROOT + '/elsewhere/a:b.pdf'
        with self.assertRaises(ReaderError):
            self.r.validate(entry)

    def test_metadata_revision_and_hash(self):
        for field, value in [('rev','changed'), ('content_hash','changed'), ('path_lower', ROOT+'/other.pdf')]:
            entry=item(); received=dict(entry);received[field]=value
            self.r._post=Mock(return_value=(b'pdf', {'Dropbox-API-Result':json.dumps(received)}))
            with self.subTest(field=field), self.assertRaises(ReaderError): self.r.download(entry,self.base/'downloads')
    def test_download_direct_file_metadata(self):
        entry=item();received=dict(entry);received.pop('.tag')
        self.r._post=Mock(return_value=(b'pdf',{'Dropbox-API-Result':json.dumps(received)}))
        self.assertEqual(self.r.download(entry,self.base/'downloads').read_bytes(),b'pdf')
    def test_body_hash_mismatch(self):
        self.r._post=Mock(return_value=(b'bad', {'Dropbox-API-Result':json.dumps(item())}))
        with self.assertRaises(ReaderError): self.r.download(item(),self.base/'downloads')
    def test_private_collision_safe_download(self):
        paths=[]
        for path in [ROOT+'/a/a.pdf', ROOT+'/b/a.pdf']:
            entry=item(path)
            self.r._post=Mock(return_value=(b'pdf', {'Dropbox-API-Result':json.dumps(entry)}))
            target=self.r.download(entry,self.base/'downloads');paths.append(target)
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            self.assertEqual(target.parent.stat().st_mode & 0o777, 0o700)
        self.assertNotEqual(*paths)
    def test_symlink_and_public_output_rejected(self):
        target=self.base/'link';target.symlink_to(self.base/'credentials.json')
        with self.assertRaises(ReaderError): save_private(target,b'oops')
        public=self.base/'public';public.mkdir(mode=0o755)
        with self.assertRaises(ReaderError): save_private(public/'file', b'oops')
    def test_unverified(self):
        self.r.verified=False
        with self.assertRaises(ReaderError): self.r.list_page()
    def test_size_limit(self):
        entry=item();entry['size']=101*1024*1024
        with self.assertRaises(ReaderError): self.r.download(entry,self.base/'downloads')
    def test_multiblock_hash(self):
        data=b'a'*4194304+b'b'
        expected=hashlib.sha256(hashlib.sha256(data[:4194304]).digest()+hashlib.sha256(b'b').digest()).hexdigest()
        self.assertEqual(content_hash(data), expected)
    def test_account_rejected(self):
        self.r._post=Mock(return_value=(b'{"access_token":"fake","expires_in":14400}', {}))
        self.r._rpc=Mock(return_value={'account_id':'wrong'})
        with self.assertRaises(ReaderError): self.r.connect()
    def test_only_read_rpc(self):
        with self.assertRaises(ReaderError): self.r._rpc('files/delete_v2',{})
    def test_refresh_expiry(self):
        self.r.expires_at=0
        self.r._post=Mock(side_effect=[(b'{"access_token":"new","expires_in":14400}',{}),(b'{"ok":true}',{})])
        self.assertEqual(self.r._rpc('files/get_metadata',{}),{'ok':True})
        self.assertEqual(self.r.token,'new')
        self.assertGreater(self.r.expires_at,time.monotonic())
    def test_rate_limit_redaction(self):
        err=urllib.error.HTTPError('https://example',429,'SECRET',{'Retry-After':'125'},None)
        with patch('urllib.request.urlopen',side_effect=err),self.assertRaises(ReaderError) as ctx:
            self.r._post('https://api.dropboxapi.com/2/files/get_metadata',b'',{})
        self.assertEqual(ctx.exception.retry_after,125)
        self.assertEqual(ctx.exception.status,429)
        self.assertNotIn('SECRET',str(ctx.exception))
    def test_binding_namespace_folder_identity(self):
        self.r._post=Mock(return_value=(b'{"access_token":"fake","expires_in":14400}',{}))
        for account in [{'account_id':'bound','email':'joe@asymmetric.financial','root_info':{'root_namespace_id':'wrong'}},
                        {'account_id':'bound','email':'other@example.com','root_info':{'root_namespace_id':'namespace'}}]:
            self.r._rpc=Mock(return_value=account)
            with self.assertRaises(ReaderError):self.r.connect()
        account={'account_id':'bound','email':'joe@asymmetric.financial','root_info':{'root_namespace_id':'namespace'}}
        self.r._rpc=Mock(side_effect=[account,{'.tag':'folder','id':'wrong','path_lower':ROOT,'path_display':ROOT}])
        with self.assertRaises(ReaderError):self.r.connect()
        self.assertFalse(self.r.verified)
    def test_env_incomplete_is_redacted(self):
        with self.assertRaises(ReaderError) as ctx:DropboxClient.from_env({'DROPBOX_APP_KEY':'SECRET'})
        self.assertNotIn('SECRET',str(ctx.exception))

    def test_verified_connect_and_metadata(self):
        account={'account_id':'bound','email':'joe@asymmetric.financial','root_info':{'root_namespace_id':'namespace'}}
        folder={'.tag':'folder','id':'folder','path_lower':ROOT,'path_display':ROOT}
        self.r._post=Mock(return_value=(b'{"access_token":"fake","expires_in":14400}',{}))
        self.r._rpc=Mock(side_effect=[account,folder])
        self.assertIs(self.r.connect(),self.r)
        self.r._rpc=Mock(return_value=item())
        self.assertEqual(self.r.metadata('2026/a.pdf')['rev'],'r1')
        with self.assertRaises(ReaderError):self.r.metadata('2026/b.pdf')
    def test_response_and_transport_bounds(self):
        response=Mock();response.read.return_value=b'123';response.headers={}
        cm=Mock();cm.__enter__=Mock(return_value=response);cm.__exit__=Mock(return_value=False)
        with patch('urllib.request.urlopen',return_value=cm) as call:
            self.assertEqual(self.r._post('https://api.dropboxapi.com/a',b'',{},3)[0],b'123')
            self.assertEqual(call.call_args.kwargs['timeout'],45)
            with self.assertRaises(ReaderError):self.r._post('https://api.dropboxapi.com/a',b'',{},2)
        for error in [TimeoutError('SECRET'),urllib.error.URLError('SECRET'),urllib.error.HTTPError('x',429,'SECRET',{'Retry-After':'oops'},None)]:
            with patch('urllib.request.urlopen',side_effect=error),self.assertRaises(ReaderError) as ctx:
                self.r._post('https://api.dropboxapi.com/a',b'',{})
            self.assertNotIn('SECRET',str(ctx.exception))
    def test_malformed_api_and_runtime_inputs(self):
        self.r._post=Mock(return_value=(b'bad',{}))
        with self.assertRaises(ReaderError):self.r._rpc('users/get_current_account',None,root=False)
        with self.assertRaises(ReaderError):self.r._refresh()
        with self.assertRaises(ReaderError):self.r.list_page('2026','')
        self.r._rpc=Mock(return_value={'entries':[]})
        with self.assertRaises(ReaderError):self.r.list_page('2026')
        bad=item();bad['.tag']='unknown'
        with self.assertRaises(ReaderError):self.r.validate(bad)
        bad=item();bad['rev']=''
        with self.assertRaises(ReaderError):self.r.download(bad,self.base/'downloads')
        config=dict(self.r.config);config['folder_path']='/elsewhere'
        with self.assertRaises(ReaderError):DropboxClient(config)
        env={'DROPBOX_'+k.upper():v for k,v in self.r.config.items()}
        self.assertEqual(DropboxClient.from_env(env).root,ROOT)
    def test_download_refresh_and_atomic_failure_cleanup(self):
        self.r.expires_at=0
        self.r._post=Mock(side_effect=[(b'{"access_token":"new","expires_in":14400}',{}),(b'pdf',{'Dropbox-API-Result':json.dumps(item())})])
        self.r.download(item(),self.base/'downloads')
        before=set(self.base.iterdir())
        with patch('os.replace',side_effect=OSError('disk failed')),self.assertRaises(OSError):save_private(self.base/'new',b'data')
        self.assertEqual(set(self.base.iterdir()),before)
