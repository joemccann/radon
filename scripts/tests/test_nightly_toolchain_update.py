"""Maintenance must preserve an idle, working reviewer and its pinned SDK pair."""
import importlib.util
import json
from pathlib import Path
import pytest

SPEC = importlib.util.spec_from_file_location('toolchain', Path(__file__).parents[1] / 'nightly_toolchain_update.py')
toolchain = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(toolchain)


def test_manifest_pins_both_codex_packages():
    policy = {'deepsec': '2.3.8', 'codex': '0.162.1'}
    got = toolchain.package_manifest(policy)
    assert got['dependencies'] == {'deepsec': '2.3.8'}
    assert got['overrides'] == {'@openai/codex': '0.162.1', '@openai/codex-sdk': '0.162.1'}


@pytest.mark.parametrize('version', ['latest', '^0.162.1', '0.162.1;touch /tmp/x', '../package'])
def test_mutable_or_nonversion_pin_refused(version):
    with pytest.raises(ValueError):
        toolchain.package_manifest({'deepsec': '2.3.8', 'codex': version})


def test_active_or_unknown_runner_defers_update():
    assert toolchain.runners_idle('state = not running\n')
    assert not toolchain.runners_idle('state = running\n')
    assert not toolchain.runners_idle('could not find service')


def test_brew_cask_owner_never_uses_npm():
    assert toolchain.codex_update_command('/opt/homebrew/bin/brew') == ['/opt/homebrew/bin/brew', 'upgrade', '--cask', 'codex']


def test_promotion_keeps_original_for_rollback(tmp_path):
    workspace = tmp_path / 'workspace'; workspace.mkdir()
    stage = tmp_path / 'stage'; stage.mkdir()
    for name in toolchain.PACKAGE_PATHS:
        if name == 'node_modules':
            (workspace / name).mkdir(); (stage / name).mkdir()
            (workspace / name / 'old').touch(); (stage / name / 'new').touch()
        else:
            (workspace / name).write_text('old'); (stage / name).write_text('new')
    backup = toolchain.promote(workspace, stage)
    assert (workspace / 'node_modules/new').exists()
    toolchain.rollback(workspace, backup)
    assert (workspace / 'node_modules/old').exists()
    assert (workspace / 'package.json').read_text() == 'old'


def test_failed_promotion_restores_all_originals(tmp_path, monkeypatch):
    workspace = tmp_path / 'workspace'; workspace.mkdir()
    stage = tmp_path / 'stage'; stage.mkdir()
    for name in toolchain.PACKAGE_PATHS:
        (workspace / name).mkdir() if name == 'node_modules' else (workspace / name).write_text('old')
        (stage / name).mkdir() if name == 'node_modules' else (stage / name).write_text('new')
    real = Path.rename
    def fail_once(self, target):
        if self == stage / 'package-lock.json':
            raise OSError('fixture promotion failure')
        return real(self, target)
    monkeypatch.setattr(Path, 'rename', fail_once)
    with pytest.raises(OSError):
        toolchain.promote(workspace, stage)
    assert (workspace / 'package.json').read_text() == 'old'
    assert (workspace / 'package-lock.json').read_text() == 'old'
    assert (workspace / 'node_modules').is_dir()


def test_clean_environment_drops_provider_and_node_injection(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'fixture')
    monkeypatch.setenv('NODE_OPTIONS', '--require=untrusted')
    assert 'OPENAI_API_KEY' not in toolchain.clean_environment()
    assert 'NODE_OPTIONS' not in toolchain.clean_environment()


def test_stage_uses_only_reviewed_lockfile(tmp_path, monkeypatch):
    package = tmp_path / 'reviewed'; package.mkdir()
    policy = {'deepsec': '2.3.8', 'codex': '0.162.1'}
    (package / 'package.json').write_text(json.dumps(toolchain.package_manifest(policy)))
    (package / 'package-lock.json').write_text('{"lockfileVersion":3}')
    calls = []
    monkeypatch.setattr(toolchain, 'run', lambda args, **kw: calls.append(args) or '')
    monkeypatch.setattr(toolchain, 'smoke', lambda *args, **kw: None)
    stage = toolchain.stage_release(tmp_path / 'releases', policy, package)
    assert calls == [['/opt/homebrew/bin/npm', 'ci', '--ignore-scripts', '--no-audit', '--no-fund']]
    assert (stage / 'package-lock.json').read_text() == '{"lockfileVersion":3}'
    assert json.loads((stage / 'verified.json').read_text())['lock_sha256']


def test_stage_refuses_policy_manifest_drift_before_install(tmp_path, monkeypatch):
    package = tmp_path / 'reviewed'; package.mkdir()
    (package / 'package.json').write_text('{}')
    calls = []
    monkeypatch.setattr(toolchain, 'run', lambda args, **kw: calls.append(args))
    with pytest.raises(RuntimeError, match='manifest differs'):
        toolchain.stage_release(tmp_path / 'releases', {'deepsec':'2.3.8','codex':'0.162.1'}, package)
    assert calls == []


def test_desktop_defers_only_busy_tool(monkeypatch):
    calls=[]
    monkeypatch.setattr(toolchain, 'tool_busy', lambda *a, **k: True)
    monkeypatch.setattr(toolchain, 'run', lambda args, **kw: calls.append(args) or ('codex' if args[1:]==['list','--cask'] else ''))
    assert toolchain.update_desktop({'desktop_casks': {'codex': None}}) == [{'tool':'codex','status':'deferred-running-tool'}]
    assert not any('upgrade' in x for x in calls)


def test_desktop_codex_updates_homebrew_owner(monkeypatch):
    monkeypatch.setattr(toolchain, 'tool_busy', lambda *a, **kw: False)
    calls = []
    def fake_run(args, **kw):
        calls.append(args)
        return 'codex' if args[1:] == ['list', '--cask'] else ''
    monkeypatch.setattr(toolchain, 'run', fake_run)
    assert toolchain.update_desktop({'desktop_casks': {'codex': None}})[0]['status'] == 'updated-or-current'
    assert ['/opt/homebrew/bin/brew', 'upgrade', '--cask', 'codex'] in calls
    assert all('npm' not in str(x) for x in calls)


def test_missing_app_manager_does_not_claim_update(monkeypatch):
    monkeypatch.setattr(toolchain, 'tool_busy', lambda *a, **kw: False)
    monkeypatch.setattr(toolchain, 'run', lambda *a, **k: '')
    assert toolchain.update_desktop({'desktop_casks': {'codex': None}}) == [{'tool': 'codex', 'status': 'not-managed-by-homebrew'}]


def test_automatic_policy_accepts_forward_stable_pair_only():
    base = {'deepsec':'2.3.8','codex':'0.162.1','smoke_model':'gpt-6.1-sol','automatic_stable_updates':True}
    assert toolchain.candidate_allowed(dict(base, deepsec='2.4.0',codex='0.163.0'), base)
    assert not toolchain.candidate_allowed(dict(base, deepsec='3.0.0'), base)
    assert not toolchain.candidate_allowed(dict(base, codex='0.145.0'), base)
    assert not toolchain.candidate_allowed(dict(base, codex='latest'), base)
    assert not toolchain.candidate_allowed(dict(base, smoke_model='unapproved'), base)
    assert not toolchain.candidate_allowed(dict(base, codex='0.163.0'), dict(base,automatic_stable_updates=False))


def test_resolver_requires_matching_published_sdk(monkeypatch):
    calls=[]
    def fake(args, **kwargs):
        calls.append(args)
        return '2.3.9' if args[2] == 'deepsec' else '0.163.0'
    monkeypatch.setattr(toolchain,'run',fake)
    got=toolchain.resolved_policy({'deepsec':'2.3.8','codex':'0.162.1'})
    assert got == {'deepsec':'2.3.9','codex':'0.163.0'}
    assert calls[-1][2] == '@openai/codex-sdk@0.163.0'
    assert all('--registry=https://registry.npmjs.org' in args for args in calls)


@pytest.mark.parametrize('version',['latest','3.0.0','2.3.7'])
def test_resolver_refuses_downgrade_or_unreviewed_major(monkeypatch,version):
    monkeypatch.setattr(toolchain,'run',lambda *a,**k:version)
    with pytest.raises(RuntimeError):
        toolchain.resolved_policy({'deepsec':'2.3.8','codex':'0.162.1'})


def test_private_ensure_refuses_operator_workspace_before_network(tmp_path, monkeypatch):
    monkeypatch.setattr(toolchain, 'run', lambda *a, **k: pytest.fail('scope must be checked first'))
    with pytest.raises(RuntimeError, match='dedicated bot runner'):
        toolchain.require_owned_deepsec(tmp_path)


def test_private_ensure_promotes_only_smoked_candidate_and_preserves_external_rollback(tmp_path, monkeypatch):
    state = tmp_path / 'state'; state.mkdir()
    workspace = tmp_path / 'clone/.deepsec'; workspace.mkdir(parents=True)
    for name in toolchain.PACKAGE_PATHS:
        (workspace/name).mkdir() if name == 'node_modules' else (workspace/name).write_text('old')
    monkeypatch.setattr(toolchain, 'require_owned_deepsec', lambda w: state)
    attempts=[]
    def smoke(path, policy, **kw):
        attempts.append(path)
        if path == workspace and len(attempts) == 1: raise RuntimeError('old reviewer fails')
    monkeypatch.setattr(toolchain, 'smoke', smoke)
    def stage(root, policy, package):
        stage=root/'candidate';stage.mkdir(parents=True)
        for name in toolchain.PACKAGE_PATHS:
            (stage/name).mkdir() if name == 'node_modules' else (stage/name).write_text('new')
        smoke(stage,policy,inference=True)
        return stage
    monkeypatch.setattr(toolchain, 'stage_release', stage)
    result=toolchain.ensure_deepsec(workspace,{'deepsec':'2.3.8','codex':'0.162.1'},tmp_path)
    assert result['status'] == 'reviewer-recovered'
    assert Path(result['backup']).parent == state/'toolchain-rollback'
    assert (workspace/'package.json').read_text() == 'new'
    assert attempts[0] == workspace and attempts[-1] == workspace


def test_private_ensure_failed_postpromotion_smoke_rolls_back(tmp_path, monkeypatch):
    state=tmp_path/'state';state.mkdir();workspace=tmp_path/'clone/.deepsec';workspace.mkdir(parents=True)
    for name in toolchain.PACKAGE_PATHS:
        (workspace/name).mkdir() if name=='node_modules' else (workspace/name).write_text('old')
    monkeypatch.setattr(toolchain,'require_owned_deepsec',lambda w:state)
    def smoke(path,*a,**k):
        if path == workspace: raise RuntimeError('fixture reviewer failed')
    monkeypatch.setattr(toolchain,'smoke',smoke)
    def stage(root,*a):
        out=root/'candidate';out.mkdir(parents=True)
        for name in toolchain.PACKAGE_PATHS:
            (out/name).mkdir() if name=='node_modules' else (out/name).write_text('new')
        return out
    monkeypatch.setattr(toolchain,'stage_release',stage)
    with pytest.raises(RuntimeError,match='fixture reviewer failed'):
        toolchain.ensure_deepsec(workspace,{'deepsec':'2.3.8','codex':'0.162.1'},tmp_path)
    assert (workspace/'package.json').read_text() == 'old'


def test_managed_codex_daemon_does_not_block_shared_cli_update(monkeypatch):
    monkeypatch.setattr(toolchain, 'run', lambda args, **k: '/Users/operator/.codex/packages/app-server-daemon/releases/0.163.0/bin/codex\n/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex' if args[0]=='/bin/ps' else 'state = not running')
    assert not toolchain.tool_busy('codex')


def test_active_shared_codex_does_not_block_unrelated_app(monkeypatch):
    def fake(args, **kw):
        if args[0]=='/bin/ps': return '/opt/homebrew/Caskroom/codex/0.162.1/bin/codex'
        if args[1:]==['list','--cask']: return 'codex\nobsidian'
        if args[0]=='/bin/launchctl': return 'state = not running'
        return ''
    calls=[]
    def capture(args,**kw): calls.append(args);return fake(args,**kw)
    monkeypatch.setattr(toolchain,'run',capture)
    result=toolchain.update_desktop({'desktop_casks':{'codex':None,'obsidian':'Obsidian.app'}})
    assert result == [{'tool':'codex','status':'deferred-running-tool'},{'tool':'obsidian','status':'updated-or-current'}]
    assert ['/opt/homebrew/bin/brew','upgrade','--cask','obsidian'] in calls
    assert ['/opt/homebrew/bin/brew','upgrade','--cask','codex'] not in calls


def test_bot_native_action_never_invokes_homebrew(tmp_path,monkeypatch):
    import contextlib
    from types import SimpleNamespace
    home=tmp_path/'bot';home.mkdir();binary=home/'.local/bin/claude';binary.parent.mkdir(parents=True);binary.touch()
    monkeypatch.setattr(Path,'home',classmethod(lambda cls:home))
    monkeypatch.setattr(toolchain.pwd,'getpwuid',lambda uid:SimpleNamespace(pw_name='_radonbot',pw_dir=str(home)))
    gates=[]
    monkeypatch.setattr(toolchain,'maintenance_gate',lambda policy:gates.append(policy) or contextlib.nullcontext())
    monkeypatch.setattr(toolchain,'tool_busy',lambda *a,**k:False)
    monkeypatch.setattr(toolchain,'update_desktop',lambda *a:pytest.fail('bot native must not invoke Homebrew'))
    calls=[];monkeypatch.setattr(toolchain,'run',lambda args,**k:calls.append(args) or '')
    policy=tmp_path/'policy.json';policy.write_text('{}')
    assert toolchain.main(['native','--policy',str(policy),'--release-root',str(tmp_path/'state')])==0
    assert gates == [{}] and calls == [[str(binary),'update']]


def test_bot_native_refuses_operator_account_before_gate(tmp_path,monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(toolchain.pwd,'getpwuid',lambda uid:SimpleNamespace(pw_name='operator'))
    monkeypatch.setattr(toolchain,'maintenance_gate',lambda *a:pytest.fail('wrong account must refuse before gate'))
    with pytest.raises(RuntimeError,match='dedicated bot account'):
        toolchain.main(['native','--policy',str(tmp_path/'missing-policy')])


@pytest.mark.parametrize('status',['Logged in using API key: fixture-redacted','Not logged in','OAuth status unknown','Not logged in using ChatGPT',''])
def test_sdk_requires_verified_subscription_auth_class(status):
    with pytest.raises(RuntimeError,match='ChatGPT subscription'):
        toolchain.require_subscription_status(status)


def test_sdk_accepts_normal_chatgpt_status():
    toolchain.require_subscription_status('Logged in using ChatGPT\n')


def test_stored_api_login_is_refused_before_model_inference(tmp_path,monkeypatch):
    policy={'deepsec':'2.3.8','codex':'0.162.1','smoke_model':'gpt-6.1-sol'}
    for package,version in [('deepsec','2.3.8'),('@openai/codex','0.162.1'),('@openai/codex-sdk','0.162.1')]:
        path=tmp_path/'node_modules'/package/'package.json';path.parent.mkdir(parents=True);path.write_text(json.dumps({'version':version}))
    def fake(args,**kw):
        if args[0]=='/opt/homebrew/bin/node':pytest.fail('API-key credential must never reach model inference')
        return 'Logged in using API key: fixture-redacted' if args[1:]==['login','status'] else 'codex-cli 0.162.1'
    monkeypatch.setattr(toolchain,'run',fake)
    with pytest.raises(RuntimeError,match='ChatGPT subscription'):
        toolchain.smoke(tmp_path,policy,inference=True)
