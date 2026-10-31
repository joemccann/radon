"""A committed deployment can retry publication without destructive rollback."""
import json
import subprocess
from pathlib import Path
import pytest
from test_deploy_corrections import (TestRootHelper as _RootFixture, ROOT_HELPER, DEPLOY, _transition_env, _write_transition_journal, _create_release_artifacts)
SHA='2'*40

@pytest.mark.parametrize('failure',['export','cleanup'])
def test_root_commit_receipt_survives_failure_and_retry(tmp_path,failure):
    env,_,_,_,active=_RootFixture()._root_helper_fixture(tmp_path)
    env['RADON_TEST_COMMIT_SHA']=SHA
    absent=subprocess.run(['bash',str(ROOT_HELPER),'verify-committed'],env=env,capture_output=True,text=True)
    assert absent.returncode==3
    for verb in ['stop-clean','recover']:
        result=subprocess.run(['bash',str(ROOT_HELPER),verb],env=env,capture_output=True,text=True)
        assert result.returncode==0,result.stderr
    executable=tmp_path/'failing-step'
    executable.write_text('#!/bin/bash\nexit 69\n');executable.chmod(0o755)
    failed_env={**env, 'RADON_TEST_MODEL_CLIENT_RUNTIME' if failure=='export' else 'RADON_TEST_RM':str(executable)}
    first=subprocess.run(['bash',str(ROOT_HELPER),'commit-transition'],env=failed_env,capture_output=True,text=True)
    assert first.returncode==69,first.stderr
    proof=subprocess.run(['bash',str(ROOT_HELPER),'verify-committed'],env=env,capture_output=True,text=True)
    assert proof.returncode==0 and proof.stdout.strip()==SHA,proof.stderr
    retry=subprocess.run(['bash',str(ROOT_HELPER),'commit-transition'],env=env,capture_output=True,text=True)
    assert retry.returncode==0,retry.stderr
    assert not active.exists()
    changed=subprocess.run(['bash',str(ROOT_HELPER),'verify-committed'],env={**env,'RADON_TEST_COMMIT_SHA':'3'*40},capture_output=True,text=True)
    assert changed.returncode==0 and changed.stdout.strip()==SHA


def test_committed_export_retry_never_rolls_back_after_health_changes(tmp_path):
    releases=tmp_path/'releases';releases.mkdir();live=tmp_path/'live';stage=releases/'stage.target';stage.mkdir();backup=releases/'backup.previous';backup.mkdir()
    journal=tmp_path/'transition.json';calls=tmp_path/'calls';_create_release_artifacts(live,'target');_create_release_artifacts(backup,'old')
    _write_transition_journal(journal,SHA,'1'*40,stage,backup,'verified')
    def run(fail):
        shell=f'''set -euo pipefail
source {DEPLOY}
current_commit() {{ printf '%s\\n' {SHA}; }}
deploy_gate() {{ printf 'GATE\\n' >> {calls}; return 1; }}
sudo() {{
  printf '%s\\n' "$*" >> {calls}
  case "$2" in
    verify-committed) printf '%s\\n' {SHA}; return 0 ;;
    commit-transition) return {69 if fail else 0} ;;
    *) return 88 ;;
  esac
}}
recover_pending_transition
'''
        return subprocess.run(['bash','-c',shell],env=_transition_env(live,releases,journal),capture_output=True,text=True)
    first=run(True)
    assert first.returncode==69,first.stderr
    assert json.loads(journal.read_text())['phase']=='finalization_pending'
    result=run(False)
    assert result.returncode==0,result.stderr
    assert not journal.exists()
    operations=calls.read_text()
    assert 'GATE' not in operations and 'stop-clean' not in operations and ' recover' not in operations


@pytest.mark.parametrize('receipt,current',[(SHA,'3'*40),('4'*40,SHA)])
def test_present_commit_receipt_identity_change_never_reenters_rollback(tmp_path,receipt,current):
    releases=tmp_path/'releases';releases.mkdir();live=tmp_path/'live';stage=releases/'stage.target';stage.mkdir();backup=releases/'backup.previous';backup.mkdir()
    journal=tmp_path/'transition.json';calls=tmp_path/'calls';_create_release_artifacts(live,'target');_create_release_artifacts(backup,'old')
    _write_transition_journal(journal,SHA,'1'*40,stage,backup,'verified')
    shell=f"""set -euo pipefail
source {DEPLOY}
current_commit() {{ printf '%s\\n' {current}; }}
deploy_gate() {{ return 1; }}
sudo() {{
  printf '%s\\n' "$*" >> {calls}
  if [[ "$2" == verify-committed ]]; then printf '%s\\n' {receipt}; return 0; fi
  return 88
}}
recover_pending_transition
"""
    result=subprocess.run(['bash','-c',shell],env=_transition_env(live,releases,journal),capture_output=True,text=True)
    assert result.returncode==78,result.stderr
    assert 'stop-clean' not in calls.read_text() and ' recover' not in calls.read_text()
    assert journal.exists()
