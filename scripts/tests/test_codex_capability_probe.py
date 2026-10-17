"""A new CLI release must expose no tools on an actual intercepted request."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import pytest

SPEC=importlib.util.spec_from_file_location('probe',Path(__file__).parents[1]/'codex_capability_probe.py')
probe=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(probe)

SCRIPT=r'''
import json, re, sys, urllib.request
args=sys.argv[1:]
mode=next((x.split('=',1)[1] for x in args if x.startswith('fixture_mode=')),'empty')
if mode=='help':print('help only');raise SystemExit(0)
schema=json.load(open(args[args.index('--output-schema')+1]))
assert schema=={'type':'object','properties':{'approved':{'type':'boolean'}},'required':['approved'],'additionalProperties':False}
base=re.search(r'base_url="([^"]+)"',args[-2]).group(1)
body={'tools':[]}
if mode=='absent':body={}
if mode=='null':body={'tools':None}
if mode=='tool':body={'tools':[{'type':'function','name':'shell'}]}
if mode=='false':body={'tools':False}
if mode=='embedded':body={'tools':[],'input':[{'type':'additional_tools','tools':[{'type':'namespace','name':'functions'}]}]}
for i in range(2 if mode=='multiple' else 1):
 data=b'{' if mode=='malformed' else json.dumps(body).encode()
 try:urllib.request.urlopen(urllib.request.Request(base+'/responses',data=data,headers={'Content-Type':'application/json'}),timeout=3)
 except urllib.error.HTTPError:pass
raise SystemExit(1)
'''


@pytest.fixture
def setup(tmp_path):
    binary=tmp_path/'codex';binary.write_text('#!'+sys.executable+'\n'+SCRIPT);binary.chmod(0o700)
    schema=tmp_path/'schema.json';schema.write_text(json.dumps({'type':'object','properties':{'approved':{'type':'boolean'}},'required':['approved'],'additionalProperties':False}))
    options=['-a','never','exec','--ignore-user-config','--ignore-rules','--ephemeral','--skip-git-repo-check','--sandbox','read-only','--json','--model','gpt-6.1-sol','-C',str(tmp_path),'--output-schema',str(schema),'--output-last-message',str(tmp_path/'out.json'),'--disable','shell_tool','--disable','apps']
    return binary.resolve(),options,tmp_path


def test_observed_empty_tools_passes_even_when_cli_exits_after_fixture_error(setup):
    binary,options,directory=setup
    probe.assert_tool_free(binary,options,directory)


@pytest.mark.parametrize('mode',['null','false','tool','multiple','malformed','help','embedded'])
def test_unsupported_or_unobserved_capabilities_never_pass(setup,mode):
    binary,options,directory=setup
    with pytest.raises(RuntimeError,match='empty tool surface'):
        probe.assert_tool_free(binary,[*options,'-c','fixture_mode='+mode],directory)


def test_fake_auth_clean_env_and_identical_options_are_used(setup,monkeypatch):
    binary,options,directory=setup
    monkeypatch.setenv('GH_TOKEN','fixture-never-forwarded');monkeypatch.setenv('SSH_AUTH_SOCK','fixture-never-forwarded')
    original=probe.subprocess.Popen;seen=[]
    def capture(args,**kwargs):
        seen.append((args,kwargs));return original(args,**kwargs)
    monkeypatch.setattr(probe.subprocess,'Popen',capture)
    probe.assert_tool_free(binary,options,directory)
    args,kwargs=seen[0]
    assert args[0]==str(binary) and args[-1]=='-'
    assert '--disable' in args and 'shell_tool' in args and 'apps' in args
    assert set(kwargs['env'])=={'HOME','CODEX_HOME','PATH','LANG','OPENAI_API_KEY','DISABLE_AUTOUPDATER'}
    assert kwargs['env']['HOME']==kwargs['env']['CODEX_HOME']!=str(Path.home())
    assert kwargs['start_new_session'] is True
    assert not list(directory.glob('capability-*'))


def test_symlinked_binary_is_refused_before_execution(setup):
    binary,options,directory=setup
    link=directory/'mutable-current';link.symlink_to(binary)
    with pytest.raises(ValueError,match='resolved executable'):
        probe.assert_tool_free(link,options,directory)


def test_complete_config_is_required_before_execution(setup):
    binary,options,directory=setup
    with pytest.raises(ValueError,match='complete structured'):
        probe.assert_tool_free(binary,[x for x in options if x!='--ignore-user-config'],directory)


def test_native_optional_tools_absence_is_safe_without_embedded_tools(setup):
    binary, options, directory = setup
    probe.assert_tool_free(binary, [*options, '-c', 'fixture_mode=absent'], directory)


def test_catalog_restricts_models_without_replacing_their_identity():
    catalog = probe.tool_free_catalog(['gpt-6.1-sol', 'gpt-6-luna'])
    assert [model['slug'] for model in catalog['models']] == ['gpt-6.1-sol', 'gpt-6-luna']
    assert all(model['tool_mode'] == 'direct' and model['shell_type'] == 'disabled' and model['apply_patch_tool_type'] is None and model['multi_agent_version'] == 'disabled' for model in catalog['models'])


def test_probe_copies_the_exact_live_catalog_bytes(setup, monkeypatch):
    binary, options, directory = setup
    catalog = directory / 'live-catalog.json'
    payload = json.dumps(probe.tool_free_catalog(['gpt-6.1-sol'])).encode()
    catalog.write_bytes(payload)
    original = probe.subprocess.Popen; observed = []
    def capture(args, **kwargs):
        field = next(value for value in args if value.startswith('model_catalog_json='))
        copied = Path(json.loads(field.split('=', 1)[1]))
        observed.append(copied.read_bytes())
        assert copied != catalog
        return original(args, **kwargs)
    monkeypatch.setattr(probe.subprocess, 'Popen', capture)
    probe.assert_tool_free(binary, [*options, '-c', 'model_catalog_json=' + json.dumps(str(catalog))], directory)
    assert observed == [payload]


def signing_fixture(monkeypatch):
    from types import SimpleNamespace
    seen = []
    def verify(args, **kwargs):
        seen.append(args)
        return SimpleNamespace(returncode=0, stdout=b'', stderr=b'')
    monkeypatch.setattr(probe.subprocess, 'run', verify)
    return seen


def test_snapshot_survives_homebrew_source_replacement(setup, monkeypatch):
    binary, options, directory = setup
    calls = signing_fixture(monkeypatch)
    source_parent = directory / 'homebrew'; source_parent.mkdir(mode=0o775); source_parent.chmod(0o775)
    source = source_parent / 'codex'; source.write_bytes(binary.read_bytes()); source.chmod(0o755)
    snapshot = probe.snapshot_binary(source.resolve(), directory)
    initial = snapshot.read_bytes()
    source.write_bytes(b'replaced by package updater')
    assert snapshot.read_bytes() == initial
    assert snapshot.stat().st_mode & 0o777 == 0o500
    assert snapshot.parent.stat().st_mode & 0o777 == 0o500
    assert snapshot.parent.name == probe.hashlib.sha256(initial).hexdigest()
    assert calls and '2DC432GLL2' in calls[0][-2]


def test_unsigned_snapshot_is_rejected(setup, monkeypatch):
    from types import SimpleNamespace
    binary, options, directory = setup
    monkeypatch.setattr(probe.subprocess, 'run', lambda *a, **kw: SimpleNamespace(returncode=1))
    with pytest.raises(RuntimeError, match='signature'): probe.snapshot_binary(binary, directory)
    assert not list((directory / 'reviewer-binaries').glob('*/codex'))


def test_writable_source_binary_is_rejected(setup, monkeypatch):
    binary, options, directory = setup
    signing_fixture(monkeypatch); binary.chmod(0o777)
    with pytest.raises(PermissionError): probe.snapshot_binary(binary, directory)


def test_existing_snapshot_tampering_is_rejected(setup, monkeypatch):
    binary, options, directory = setup; signing_fixture(monkeypatch)
    snapshot = probe.snapshot_binary(binary, directory)
    snapshot.chmod(0o700); snapshot.write_bytes(b'tampered'); snapshot.chmod(0o500)
    with pytest.raises(RuntimeError, match='digest'): probe.snapshot_binary(binary, directory)


@pytest.mark.parametrize('stdout, stderr, code, accepted', [(b'', b'Logged in using ChatGPT\n', 0, True), (b'Logged in using an API key\n', b'', 0, False), (b'', b'Logged in using ChatGPT\n', 1, False), (b'', b'unknown status', 0, False)])
def test_subscription_login_status_captures_stderr_privately(setup, monkeypatch, stdout, stderr, code, accepted):
    from types import SimpleNamespace
    binary, options, directory = setup
    monkeypatch.setattr(probe.subprocess, 'run', lambda *a, **kw: SimpleNamespace(returncode=code, stdout=stdout, stderr=stderr))
    if accepted: probe.assert_subscription(binary, {'HOME': str(directory)})
    else:
        with pytest.raises(RuntimeError, match='subscription'): probe.assert_subscription(binary, {'HOME': str(directory)})
