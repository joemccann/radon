"""Cutover copies account clocks before managed ephemeral files disappear."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import shutil
import pytest
CLOUD=Path(__file__).resolve().parents[1]
def module():
    spec=importlib.util.spec_from_file_location('pacing_capture',CLOUD/'scripts/model_pacing.py');result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result);return result

@pytest.mark.parametrize("language",["python","node"])
def test_uid_reader_exports_only_numeric_clocks_and_preserves_trip_sidecar(tmp_path,language):
    api=module();path=tmp_path/'rate.json';path.write_text(json.dumps({'next_at':91,'tripped_until':100,'provider_key':'DO_NOT_EXPORT'}));(tmp_path/'rate.json.tripped').write_text('150\n')
    command=[sys.executable,'-I','-c',api.READER] if language=='python' else [shutil.which('node'),'--eval',api.NODE_READER]
    result=subprocess.run(command,env={**os.environ,'RADON_NVIDIA_RATE_STATE':str(path)},capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)=={'next_at':91,'tripped_until':150}
    assert 'DO_NOT_EXPORT' not in result.stdout

@pytest.mark.parametrize('bad',[{'next_at':float('inf')},{'next_at':'private'},{'consecutive_429':True}])
def test_uid_reader_refuses_malformed_clocks(tmp_path,bad):
    path=tmp_path/'rate.json';path.write_text(json.dumps(bad))
    result=subprocess.run([sys.executable,'-I','-c',module().READER],env={**os.environ,'RADON_NVIDIA_RATE_STATE':str(path)},capture_output=True,text=True)
    assert result.returncode==78 and result.stdout==''

def test_capture_staging_never_weakens_an_existing_account_deadline(tmp_path):
    api=module();root=tmp_path/'imports';root.mkdir(mode=0o700)
    api.store(root,'host.json',{'next_at':200,'tripped_until':400},uid=os.getuid(),gid=os.getgid())
    api.store(root,'host.json',{'next_at':20,'tripped_until':40},uid=os.getuid(),gid=os.getgid())
    assert json.loads((root/'host.json').read_text())=={'next_at':200,'tripped_until':400}

def test_root_lifecycle_captures_account_state_before_any_service_teardown():
    helper=(CLOUD/'scripts/deploy-root-helper.sh').read_text().rsplit('  stop-clean)\n',1)[1].split('    ;;',1)[0]
    assert helper.index('capture-model-pacing')<helper.index('snapshot_active_units')<helper.index('stop_release_consumers')
    runtime=(CLOUD/'scripts/radon-app-runtime.sh').read_text()
    assert 'model_pacing.py' in runtime and '/source/nvidia-pacing:ro' in runtime


def test_rate_state_capture_joins_the_existing_account_lock_without_reset(tmp_path):
    import fcntl,time
    api=module();path=tmp_path/'rate.json';path.write_text('{"next_at":99}')
    lock=(tmp_path/'rate.json.lock').open('w');fcntl.flock(lock,fcntl.LOCK_EX)
    started=time.monotonic()
    try:
        result=subprocess.run([sys.executable,'-I','-c',api.READER],env={**os.environ,'RADON_NVIDIA_RATE_STATE':str(path)},capture_output=True,text=True,timeout=2)
    finally:lock.close()
    assert result.returncode==78 and result.stdout==''
    assert time.monotonic()-started<1
    assert json.loads(path.read_text())=={'next_at':99}


def test_specific_unit_capture_occurs_immediately_before_container_stop():
    text=(CLOUD/'scripts/radon-app-runtime.sh').read_text();body=text.split('cmd_halt() {',1)[1].split('\n}',1)[0]
    assert 'capture-model-pacing' in body or 'model_pacing.py' in body
    assert body.index('model_pacing.py')<body.index('\"$DOCKER\" stop ')
