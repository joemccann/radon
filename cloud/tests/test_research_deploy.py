"""Optional research lifecycle stays inert until explicitly enabled."""
from pathlib import Path
import os
import re
import subprocess
import pytest

HELPER=Path(__file__).resolve().parents[1]/'scripts/deploy-root-helper.sh'

@pytest.mark.parametrize('enabled',['enabled','enabled-runtime','disabled','not-found'])
def test_optional_research_is_started_only_when_enabled(tmp_path,enabled):
    text=HELPER.read_text()
    body=re.search(r'^start_optional_research\(\) \{\n(.*?)^\}',text,re.M|re.S).group(0)
    log=tmp_path/'calls'
    script='''systemctl_bounded() {
if [[ "$1" == "is-enabled" ]]; then echo "$ENABLED"; [[ "$ENABLED" != "not-found" ]]; return; fi
printf '%s\\n' "$*" >> "$CALLS"
}
wait_for_unit_state() { printf 'wait %s\\n' "$*" >> "$CALLS"; }
'''+body+'\nstart_optional_research\n'
    result=subprocess.run(['bash','-c',script],env={**os.environ,'ENABLED':enabled,'CALLS':str(log)},capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    calls=log.read_text() if log.exists() else ''
    if enabled.startswith('enabled'):
        assert calls.splitlines()==['reset-failed radon-research.service','--no-block start radon-research.service','wait radon-research.service active']
    else:assert not calls
    assert 'enable --now' not in body


def test_fresh_setup_installs_research_but_does_not_enable_it(tmp_path):
    setup = (HELPER.parent / 'setup-vps.sh').read_text()
    inventory = re.search(r'readonly SERVICE_FILES=\(.*?\n\)', setup, re.S).group(0)
    assert 'radon-research.service' in inventory
    body = re.search(r'^enable_services\(\) \{\n(.*?)^\}', setup, re.M | re.S).group(0)
    log = tmp_path / 'calls'
    script = 'SERVICE_FILES=(radon-api.service radon-research.service)\nlog_info() { :; }\nlog_success() { :; }\nsystemctl() { printf \'%s\\n\' "$*" >> "$CALLS"; }\n' + body + '\nenable_services\n'
    result = subprocess.run(['bash', '-c', script], env={**os.environ, 'CLOUD_DIR': str(tmp_path), 'CALLS': str(log)}, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert 'radon-api.service' in log.read_text()
    assert 'radon-research.service' not in log.read_text()


def _state_wait_result(unit, desired, ready_at, *, test_mode=False, failed=False):
    """Exercise the real poller with a simulated clock, without wall-clock sleeps."""
    helper = HELPER.read_text()
    poller = re.search(r'^wait_for_unit_state\(\) \{\n(.*?)^\}', helper, re.M | re.S).group(0)
    # Read production budgets, including any future dedicated state budgets.
    constants = '\n'.join(re.findall(r'^  readonly (\w*WAIT_SECONDS=\d+)$', helper, re.M))
    script = constants + '\n' + poller + '''
unset SECONDS
SECONDS=0
SLEEP=advance_clock
advance_clock() { SECONDS=$((SECONDS + 1)); }
active_state() {
  if (( FAILED == 1 )); then echo failed
  elif (( SECONDS >= READY_AT )); then echo "$DESIRED"
  else echo deactivating
  fi
}
if (( HELPER_TEST_MODE == 1 )); then STATE_WAIT_SECONDS=0; fi
wait_for_unit_state "$UNIT" "$DESIRED"
rc=$?
printf 'elapsed=%s\\n' "$SECONDS"
exit "$rc"
'''
    return subprocess.run(
        ['bash', '-c', script], capture_output=True, text=True, timeout=5,
        env={**os.environ, 'UNIT': unit, 'DESIRED': desired, 'READY_AT': str(ready_at),
             'HELPER_TEST_MODE': str(int(test_mode)), 'FAILED': str(int(failed))},
    )


def test_research_stop_allows_systemd_timeout_and_container_cleanup():
    result = _state_wait_result('radon-research.service', 'inactive', 125)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'elapsed=125'


@pytest.mark.parametrize(('unit', 'desired', 'test_mode', 'expected_elapsed'), [
    ('radon-research.service', 'inactive', False, 150),
    ('radon-api.service', 'inactive', False, 60),
    ('radon-research.service', 'active', False, 60),
    ('radon-research.service', 'inactive', True, 0),
])
def test_state_wait_remains_bounded(unit, desired, test_mode, expected_elapsed):
    result = _state_wait_result(unit, desired, 1000, test_mode=test_mode)
    assert result.returncode == 71, result.stderr
    assert result.stdout.strip() == f'elapsed={expected_elapsed}'
    assert f'timed out waiting for {unit} to become {desired}' in result.stderr


def test_research_failed_is_stopped_without_waiting():
    result = _state_wait_result('radon-research.service', 'inactive', 1000, failed=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'elapsed=0'


def test_research_stop_budget_fits_service_and_supervisor_deadlines():
    helper = HELPER.read_text()
    unit = (HELPER.parents[1] / 'services/radon-research.service').read_text()
    stop_timeout = int(re.search(r'^TimeoutStopSec=(\d+)$', unit, re.M).group(1))
    wait = int(re.search(r'^  readonly RESEARCH_STOP_WAIT_SECONDS=(\d+)$', helper, re.M).group(1))
    supervisor = int(re.search(r'^  readonly ROOT_MUTATION_ACTION_TIMEOUT=(\d+)$', helper, re.M).group(1))
    assert wait >= stop_timeout + 30, 'systemd shutdown must leave time for ExecStopPost cleanup'
    assert supervisor >= wait + 30, 'the root supervisor must outlast state polling'
