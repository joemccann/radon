"""Root self-refresh accepts only deployed, exact public main and protected source."""
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import pytest

SPEC=importlib.util.spec_from_file_location('installed_update',Path(__file__).parents[1]/'runner/update_installed.py')
update=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(update)
SHA='a'*40


def run_record():
    return dict(id=7,run_attempt=1,head_sha=SHA,head_branch='main',event='push',path='.github/workflows/ci.yml',status='completed',conclusion='success')


def metadata(monkeypatch,runs,jobs=None):
    calls=[]
    def get(path):
        calls.append(path)
        return {'workflow_runs':runs} if path.startswith('actions/workflows/') else {'jobs':jobs if jobs is not None else [{'name':'Deploy to VPS','status':'completed','conclusion':'success'}]}
    monkeypatch.setattr(update,'public_json',get)
    return calls


def test_exact_main_ci_and_production_are_required(monkeypatch):
    calls=metadata(monkeypatch,[run_record()])
    assert update.approved_run(SHA)==7
    assert 'head_sha='+SHA in calls[0]
    assert 'filter=latest' in calls[1]


@pytest.mark.parametrize('field,value',[('head_sha','b'*40),('head_branch','feature'),('event','pull_request'),('path','.github/workflows/other.yml'),('status','queued'),('conclusion','failure')])
def test_wrong_or_unfinished_ci_is_never_accepted(monkeypatch,field,value):
    record=run_record();record[field]=value;metadata(monkeypatch,[record])
    with pytest.raises(RuntimeError): update.approved_run(SHA)


def test_superseded_green_does_not_hide_new_red_run(monkeypatch):
    green=run_record();new=run_record();new.update(id=8,status='in_progress',conclusion=None)
    metadata(monkeypatch,[green,new])
    with pytest.raises(RuntimeError,match='not green'):update.approved_run(SHA)


@pytest.mark.parametrize('jobs',[[],[{'name':'Deploy to VPS','status':'completed','conclusion':'skipped'}],[{'name':'Deploy to VPS','status':'in_progress','conclusion':None}]])
def test_deploy_missing_skipped_or_running_is_pending(monkeypatch,jobs):
    metadata(monkeypatch,[run_record()],jobs)
    with pytest.raises(RuntimeError,match='deployment'):update.approved_run(SHA)


@pytest.mark.parametrize('name,kind,target',[('../escape',tarfile.REGTYPE,''),('/absolute',tarfile.REGTYPE,''),('scripts/link',tarfile.SYMTYPE,'/etc'),('scripts/link',tarfile.LNKTYPE,'scripts/other'),('cloud/file',tarfile.REGTYPE,'')])
def test_archive_cannot_write_outside_known_scripts_or_follow_links(tmp_path,name,kind,target):
    stream=io.BytesIO()
    with tarfile.open(fileobj=stream,mode='w') as archive:
        entry=tarfile.TarInfo(name);entry.type=kind;entry.linkname=target;archive.addfile(entry)
    with pytest.raises(RuntimeError,match='unexpected'):
        update.extract_scripts(stream.getvalue(),tmp_path)
    assert list(tmp_path.iterdir())==[]


def test_git_transport_and_reference_are_not_supplied_by_environment(monkeypatch,tmp_path):
    calls=[]
    monkeypatch.setattr(update,'command',lambda args,**kw:calls.append((args,kw)) or '')
    update.git(tmp_path/'origin.git','fetch','--no-tags',update.ORIGIN,'refs/heads/main:refs/heads/main')
    args=calls[0][0]
    assert args[0]=='/usr/bin/git'
    assert 'core.hooksPath=/dev/null' in args and 'core.fsmonitor=false' in args
    assert update.ORIGIN=='https://github.com/joemccann/radon.git'
    assert not {'GH_TOKEN','GITHUB_TOKEN','SSH_AUTH_SOCK','SUDO_UID','PYTHONPATH'} & update.ENV.keys()
    assert update.ENV['GIT_CONFIG_GLOBAL']=='/dev/null'


def test_nonroot_cannot_reach_network_or_process_execution(monkeypatch):
    monkeypatch.setattr(update.os,'geteuid',lambda:501)
    monkeypatch.setattr(update,'public_json',lambda *a:pytest.fail('network must not run'))
    monkeypatch.setattr(update,'command',lambda *a,**k:pytest.fail('process must not run'))
    with pytest.raises(RuntimeError,match='root service'):update.refresh()


def test_root_snapshot_validation_rejects_operator_owned_source(tmp_path):
    if update.os.geteuid()==0:pytest.skip('this invariant needs an operator-owned fixture')
    with pytest.raises(RuntimeError,match='not protected'):update.protected(tmp_path)


def refresh_fixture(tmp_path,monkeypatch):
    base=tmp_path/'state';prefix=tmp_path/'installed';prefix.mkdir()
    (prefix/'maintenance.lock').touch()
    (prefix/'maintenance-ready.json').write_text(json.dumps({'version':1,'loops':list(update.LOOPS)}))
    monkeypatch.setattr(update,'BASE',base);monkeypatch.setattr(update,'PREFIX',prefix)
    monkeypatch.setattr(update.os,'geteuid',lambda:0)
    monkeypatch.setattr(update.os,'environ',{'GH_TOKEN':'fixture-not-an-auth-token','SSH_AUTH_SOCK':'fixture'})
    monkeypatch.setattr(update,'protected',lambda p:None)
    commands=[]
    def command(args,**kw):
        commands.append(args)
        if args[0]=='/bin/launchctl':return 'state = not running\n'
        return ''
    monkeypatch.setattr(update,'command',command)
    monkeypatch.setattr(update,'public_json',lambda p:{'object':{'sha':SHA}})
    monkeypatch.setattr(update,'approved_run',lambda sha:7)
    def git(store,*args,**kw):
        if args[0]=='rev-parse':return SHA
        if args[0]=='archive':return b'fixture validated by extraction stub'
        return ''
    monkeypatch.setattr(update,'git',git)
    def extract(payload,destination):
        p=destination/'scripts/runner/install.sh';p.parent.mkdir(parents=True);p.write_text('fixture')
    monkeypatch.setattr(update,'extract_scripts',extract)
    monkeypatch.setattr(update,'write_receipt',lambda p,d:p.write_text(json.dumps(d)))
    return base,commands


def test_installs_only_exact_approved_snapshot_and_clears_ambient_authority(tmp_path,monkeypatch):
    base,calls=refresh_fixture(tmp_path,monkeypatch)
    assert update.refresh()=={'status':'installed','sha':SHA,'run_id':7}
    installers=[x for x in calls if x[0]=='/bin/bash']
    assert len(installers)==1 and tuple(installers[0][2:])==update.LOOPS
    assert str(base/'source-') in installers[0][1]
    assert json.loads((base/'installed.json').read_text())['sha']==SHA
    assert 'GH_TOKEN' not in update.os.environ and 'SSH_AUTH_SOCK' not in update.os.environ


def test_same_installed_sha_never_reinstalls(tmp_path,monkeypatch):
    base,calls=refresh_fixture(tmp_path,monkeypatch);base.mkdir()
    (base/'installed.json').write_text(json.dumps({'sha':SHA}))
    assert update.refresh()=={'status':'current','sha':SHA}
    assert not any(x[0]=='/bin/bash' for x in calls)


def test_main_advancing_after_extraction_prevents_root_install(tmp_path,monkeypatch):
    base,calls=refresh_fixture(tmp_path,monkeypatch)
    seen=[]
    def main(path):
        seen.append(path);return {'object':{'sha':SHA if len(seen)==1 else 'b'*40}}
    monkeypatch.setattr(update,'public_json',main)
    with pytest.raises(RuntimeError,match='advanced before install'):update.refresh()
    assert not any(x[0]=='/bin/bash' for x in calls)
    assert not (base/'installed.json').exists()


def test_ci_rerun_after_source_validation_prevents_root_install(tmp_path,monkeypatch):
    base,calls=refresh_fixture(tmp_path,monkeypatch);attempts=[]
    def ci(sha):attempts.append(sha);return 7 if len(attempts)==1 else 8
    monkeypatch.setattr(update,'approved_run',ci)
    with pytest.raises(RuntimeError,match='CI run changed'):update.refresh()
    assert not any(x[0]=='/bin/bash' for x in calls)
    assert not (base/'installed.json').exists()


def test_failed_installer_cannot_advance_receipt(tmp_path,monkeypatch):
    base,calls=refresh_fixture(tmp_path,monkeypatch)
    original=update.command
    def fail(args,**kw):
        if args[0]=='/bin/bash':raise RuntimeError('fixture installer refused')
        return original(args,**kw)
    monkeypatch.setattr(update,'command',fail)
    with pytest.raises(RuntimeError,match='installer refused'):update.refresh()
    assert not (base/'installed.json').exists()


def test_interrupted_install_retries_without_ready_marker(tmp_path, monkeypatch):
    base, calls = refresh_fixture(tmp_path, monkeypatch)
    original = update.command
    def interrupted(args, **kw):
        if args[0] == '/bin/bash':
            (update.PREFIX / 'maintenance-ready.json').unlink()
            raise RuntimeError('fixture killed midway')
        return original(args, **kw)
    monkeypatch.setattr(update, 'command', interrupted)
    with pytest.raises(RuntimeError, match='killed midway'): update.refresh()
    assert (base / 'pending-install.json').exists()
    def retry(args, **kw):
        if args[0] == '/bin/bash':
            (update.PREFIX / 'maintenance-ready.json').write_text(json.dumps({'version': 1, 'loops': list(update.LOOPS)}))
        return original(args, **kw)
    monkeypatch.setattr(update, 'command', retry)
    assert update.refresh()['status'] == 'installed'
    assert not (base / 'pending-install.json').exists()


def test_missing_ready_without_prior_install_proof_refuses_bootstrap(tmp_path, monkeypatch):
    base, calls = refresh_fixture(tmp_path, monkeypatch)
    (update.PREFIX / 'maintenance-ready.json').unlink()
    with pytest.raises(RuntimeError, match='incomplete'): update.refresh()
    assert not calls


@pytest.mark.parametrize('change', ['sha', 'loops', 'gate_inode', 'version'])
def test_invalid_pending_install_never_authorizes_missing_ready(tmp_path, monkeypatch, change):
    base, calls = refresh_fixture(tmp_path, monkeypatch); base.mkdir()
    gate = (update.PREFIX / 'maintenance.lock').stat()
    pending = {'version': 1, 'sha': SHA, 'run_id': 7, 'ready': {'version': 1, 'loops': list(update.LOOPS)}, 'gate_device': gate.st_dev, 'gate_inode': gate.st_ino}
    if change == 'loops': pending['ready']['loops'] = ['security']
    else: pending[change] = 'invalid'
    (base / 'pending-install.json').write_text(json.dumps(pending))
    (update.PREFIX / 'maintenance-ready.json').unlink()
    with pytest.raises(RuntimeError, match='pending'): update.refresh()
    assert not calls


def test_installer_returning_without_complete_ready_keeps_pending(tmp_path, monkeypatch):
    base, calls = refresh_fixture(tmp_path, monkeypatch)
    original = update.command
    def incomplete(args, **kw):
        if args[0] == '/bin/bash': (update.PREFIX / 'maintenance-ready.json').unlink()
        return original(args, **kw)
    monkeypatch.setattr(update, 'command', incomplete)
    with pytest.raises(RuntimeError, match='incomplete'): update.refresh()
    assert (base / 'pending-install.json').exists()
    assert not (base / 'installed.json').exists()


@pytest.mark.parametrize('code, recovery, accepted', [(113, True, True), (113, False, False), (1, True, False)])
def test_only_proven_recovery_accepts_missing_launchd_service(monkeypatch, code, recovery, accepted):
    from types import SimpleNamespace
    monkeypatch.setattr(update.subprocess, 'run', lambda *a, **kw: SimpleNamespace(returncode=code, stdout=b''))
    args = ['/bin/launchctl', 'print', 'system/com.radon.runner.security']
    if accepted: assert update.command(args, allow_missing=recovery) == 'state = not running\n'
    else:
        with pytest.raises(RuntimeError): update.command(args, allow_missing=recovery)


# --- the shallow store must follow a moved main (2026-10-25) ----------------------
#
# The first refresh fetched main into BASE/origin.git with --depth=1. Every later
# refresh fetched `refs/heads/main:refs/heads/main` into that store: a shallow tip
# cannot fast-forward to a main that moved, git rejects the update and exits 1,
# and the updater logged `git failed (exit 1)` on all 31 runs. The six loop envs
# never left their 2026-10-17 shape. The refspec must force the update.


def test_refresh_fetches_main_with_a_forced_refspec(tmp_path,monkeypatch):
    refresh_fixture(tmp_path,monkeypatch)
    fetches=[]
    def git(store,*args,**kw):
        if args[0]=='fetch':fetches.append(args)
        if args[0]=='rev-parse':return SHA
        if args[0]=='archive':return b'fixture validated by extraction stub'
        return ''
    monkeypatch.setattr(update,'git',git)
    assert update.refresh()['status']=='installed'
    assert fetches==[('fetch','--no-tags','--depth=1',update.ORIGIN,update.MAIN_REFSPEC)]
    assert update.MAIN_REFSPEC=='+refs/heads/main:refs/heads/main'


def test_the_forced_refspec_follows_a_moved_main_in_a_shallow_store(tmp_path):
    import subprocess
    src=tmp_path/'src';store=tmp_path/'origin.git'
    env={'GIT_CONFIG_GLOBAL':'/dev/null','GIT_CONFIG_NOSYSTEM':'1','HOME':str(tmp_path),'PATH':'/usr/bin:/bin'}
    def src_git(*args):subprocess.run(['/usr/bin/git','-C',str(src),'-c','user.name=t','-c','user.email=t@t',*args],env=env,check=True,capture_output=True)
    subprocess.run(['/usr/bin/git','init','-q','-b','main',str(src)],env=env,check=True,capture_output=True)
    src_git('commit','-q','--allow-empty','-m','A')
    update.command(['/usr/bin/git','-c','init.templateDir=','init','--bare',str(store)])
    update.git(store,'fetch','--no-tags','--depth=1',str(src),update.MAIN_REFSPEC)
    src_git('commit','-q','--allow-empty','-m','B')
    update.git(store,'fetch','--no-tags','--depth=1',str(src),update.MAIN_REFSPEC)
    moved=subprocess.run(['/usr/bin/git','-C',str(src),'rev-parse','main'],env=env,check=True,capture_output=True,text=True).stdout.strip()
    assert update.git(store,'rev-parse','refs/heads/main')==moved
