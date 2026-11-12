"""The shared-folder research watcher is retired; deploys disable it and remove any installed copy."""
from pathlib import Path
import os
import re
import subprocess
import pytest

HELPER=Path(__file__).resolve().parents[1]/'scripts/deploy-root-helper.sh'
RETIRE_FUNCTIONS = ('retire_research_watcher', 'stop_retired_research_watcher', 'remove_retired_research_unit')
REMOVAL = ['daemon-reload', 'reset-failed radon-research.service']

STUBS = r'''set -euo pipefail
SYSTEMD_UNIT_DIR="$UNITS"
RM=rm
systemctl_bounded() {
if [[ "$1" == "is-enabled" ]]; then echo "$ENABLED"; [[ "$ENABLED" == enabled* ]]; return; fi
printf '%s\n' "$*" >> "$CALLS"
if [[ "$*" == *disable* && "$FAIL_DISABLE" == 1 ]]; then return 1; fi
}
active_state() { echo "$ACTIVE"; }
wait_for_unit_state() { printf 'wait %s\n' "$*" >> "$CALLS"; [[ "$FAIL_WAIT" == 0 ]]; }
'''


def _retire_calls(tmp_path, enabled, active, *, fail_disable=False, fail_wait=False, installed=True):
    """Run the real retire functions against a stubbed systemd and a temporary unit directory."""
    text = HELPER.read_text()
    bodies = [re.search(rf'^{name}\(\) \{{\n(.*?)^\}}', text, re.M | re.S).group(0) for name in RETIRE_FUNCTIONS]
    units = tmp_path / 'system'
    units.mkdir()
    if installed:
        (units / 'radon-research.service').write_text('[Service]\n')
        (units / 'radon-research.service.d').mkdir()
        (units / 'radon-research.service.d' / 'runtime-container.conf').write_text('[Service]\n')
    (units / 'radon-api.service').write_text('[Service]\n')
    log = tmp_path / 'calls'
    script = STUBS + '\n'.join(bodies) + '\nretire_research_watcher\necho survived\n'
    result = subprocess.run(
        ['bash', '-c', script],
        env={**os.environ, 'ENABLED': enabled, 'ACTIVE': active, 'CALLS': str(log), 'UNITS': str(units),
             'FAIL_DISABLE': '1' if fail_disable else '0', 'FAIL_WAIT': '1' if fail_wait else '0'},
        capture_output=True, text=True,
    )
    calls = log.read_text().splitlines() if log.exists() else []
    return result, calls, units


def _removed(units):
    assert not (units / 'radon-research.service').exists()
    assert not (units / 'radon-research.service.d').exists()
    assert (units / 'radon-api.service').is_file(), 'only the retired unit is removed'


def _kept(units):
    assert (units / 'radon-research.service').is_file()
    assert (units / 'radon-research.service.d' / 'runtime-container.conf').is_file()


@pytest.mark.parametrize('enabled', ['enabled', 'enabled-runtime'])
@pytest.mark.parametrize('active', ['active', 'inactive'])
def test_retire_disables_an_enabled_watcher_then_removes_its_unit(tmp_path, enabled, active):
    """MarketDesk replaced the shared-folder watcher: a deploy turns it off and uninstalls it."""
    result, calls, units = _retire_calls(tmp_path, enabled, active)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'survived'
    assert calls == [
        '--no-block disable --now radon-research.service',
        'wait radon-research.service inactive',
        *REMOVAL,
    ]
    _removed(units)


@pytest.mark.parametrize('active', ['active', 'activating'])
def test_retire_stops_a_manually_started_watcher_then_removes_its_unit(tmp_path, active):
    result, calls, units = _retire_calls(tmp_path, 'disabled', active)
    assert result.returncode == 0, result.stderr
    assert calls == [
        '--no-block stop radon-research.service',
        'wait radon-research.service inactive',
        *REMOVAL,
    ]
    _removed(units)


@pytest.mark.parametrize('enabled', ['disabled', 'not-found', 'masked', 'static'])
@pytest.mark.parametrize('active', ['inactive', 'failed', ''])
def test_retire_removes_an_installed_inactive_watcher(tmp_path, enabled, active):
    """The repository no longer ships the unit, so an installed copy would be an untracked unit."""
    result, calls, units = _retire_calls(tmp_path, enabled, active)
    assert result.returncode == 0, result.stderr
    assert calls == REMOVAL
    _removed(units)


def test_retire_is_a_noop_once_the_unit_is_gone(tmp_path):
    result, calls, units = _retire_calls(tmp_path, 'not-found', '', installed=False)
    assert result.returncode == 0, result.stderr
    assert calls == []
    assert (units / 'radon-api.service').is_file()


def test_retire_keeps_the_unit_while_the_watcher_is_still_stopping(tmp_path):
    result, calls, units = _retire_calls(tmp_path, 'disabled', 'active', fail_wait=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'survived'
    assert 'still stopping' in result.stderr
    assert 'daemon-reload' not in calls
    _kept(units)


def test_retire_is_never_fatal(tmp_path):
    result, calls, units = _retire_calls(tmp_path, 'enabled', 'active', fail_disable=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'survived'
    assert 'radon-research.service' in result.stderr
    _kept(units)


def test_deploy_never_starts_the_retired_watcher():
    text = HELPER.read_text()
    assert 'start_optional_research' not in text
    assert 'start radon-research.service' not in text
    for verb in ('restart-managed', 'recover'):
        arm = re.search(rf'^  {verb}\)\n(.*?)^    ;;', text, re.M | re.S).group(1)
        lines = [line.strip() for line in arm.splitlines()]
        assert 'retire_research_watcher' in lines, verb
        # After the snapshot replay, which would otherwise restart an
        # active-at-snapshot watcher on the first deploy.
        assert lines.index('retire_research_watcher') > lines.index('resume_active_snapshot'), verb


def test_retired_watcher_is_no_longer_shipped():
    cloud = HELPER.parents[1]
    assert not (cloud / 'services/radon-research.service').exists()
    assert not (cloud / 'services/radon-research.service.d').exists()
    setup = (HELPER.parent / 'setup-vps.sh').read_text()
    inventory = re.search(r'readonly SERVICE_FILES=\(.*?\n\)', setup, re.S).group(0)
    assert 'radon-research.service' not in inventory
    for name in ('deploy-root-helper.sh', 'bootstrap-control-plane.sh'):
        assert 'services/radon-research.service' not in (HELPER.parent / name).read_text(), name
    assert 'radon-research.service' not in (cloud / 'config/installed-units.sha256').read_text()


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
    """A leftover installed watcher still carries TimeoutStopSec=120, its last shipped body."""
    helper = HELPER.read_text()
    stop_timeout = 120
    wait = int(re.search(r'^  readonly RESEARCH_STOP_WAIT_SECONDS=(\d+)$', helper, re.M).group(1))
    supervisor = int(re.search(r'^  readonly ROOT_MUTATION_ACTION_TIMEOUT=(\d+)$', helper, re.M).group(1))
    assert wait >= stop_timeout + 30, 'systemd shutdown must leave time for ExecStopPost cleanup'
    assert supervisor >= wait + 30, 'the root supervisor must outlast state polling'


def test_fresh_setup_enables_the_marketdesk_timer_not_its_oneshot(tmp_path):
    setup = (HELPER.parent / 'setup-vps.sh').read_text()
    inventory = re.search(r'readonly SERVICE_FILES=\(.*?\n\)', setup, re.S).group(0)
    assert 'radon-marketdesk.service' in inventory
    assert 'radon-marketdesk.timer' in inventory
    body = re.search(r'^enable_services\(\) \{\n(.*?)^\}', setup, re.M | re.S).group(0)
    log = tmp_path / 'calls'
    (tmp_path / 'services').mkdir()
    (tmp_path / 'services' / 'radon-marketdesk.timer').write_text('')
    script = 'SERVICE_FILES=(radon-marketdesk.service radon-marketdesk.timer)\nlog_info() { :; }\nlog_success() { :; }\nsystemctl() { printf \'%s\\n\' "$*" >> "$CALLS"; }\n' + body + '\nenable_services\n'
    result = subprocess.run(['bash', '-c', script], env={**os.environ, 'CLOUD_DIR': str(tmp_path), 'CALLS': str(log)}, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    calls = log.read_text().splitlines()
    assert 'disable radon-marketdesk.service' in calls
    assert any(c.startswith('enable ') and 'radon-marketdesk.timer' in c for c in calls)


def _snapshot_replay(tmp_path, snapshot, states, verb):
    """Run the real resume/verify functions against a stubbed systemd."""
    text = HELPER.read_text()
    bodies = [re.search(rf'^{name}\(\) \{{\n(.*?)^\}}', text, re.M | re.S).group(0)
              for name in ('is_core_service', 'is_retired_unit', 'verify_restored_state', 'resume_active_snapshot')]
    active = tmp_path / 'active'
    active.write_text(''.join(f'{unit}\t{kind}\n' for unit, kind in snapshot))
    log = tmp_path / 'calls'
    states_file = tmp_path / 'states'
    states_file.write_text(''.join(f'{u} {s}\n' for u, s in states.items()))
    script = '''set -uo pipefail
CORE_SERVICES=(radon-api.service)
ACTIVE_STATE_FILE="$ACTIVE"
RESTORED_STATE_FILE="$ACTIVE.restored"
SYNC=true
validate_active_snapshot() { return 0; }
unit_result() { echo success; }
active_state() { awk -v u="$1" '$1==u {print $2}' "$STATES"; }
systemctl_bounded() {
  printf '%s\\n' "$*" >> "$CALLS"
  if [[ "$1 $2" == "--no-block start" ]]; then
    shift 2
    for u in "$@"; do printf '%s active\\n' "$u" >> "$STATES.new"; done
    awk 'NR==FNR {seen[$1]=1; print; next} !($1 in seen)' "$STATES.new" "$STATES" > "$STATES.tmp"
    mv "$STATES.tmp" "$STATES"; rm -f "$STATES.new"
  fi
}
wait_for_unit_state() { printf 'wait %s\\n' "$*" >> "$CALLS"; }
''' + '\n'.join(bodies) + f'\n{verb}\n'
    result = subprocess.run(['bash', '-c', script], capture_output=True, text=True, timeout=10,
                            env={**os.environ, 'ACTIVE': str(active), 'CALLS': str(log), 'STATES': str(states_file)})
    calls = log.read_text().splitlines() if log.exists() else []
    return result, calls


def test_snapshot_replay_never_restarts_the_retired_watcher(tmp_path):
    """First retiring deploy: radon-research was active at stop-clean. Replaying it would
    let its heartbeat recreate the dropbox-research row migration 0102 deleted."""
    snapshot = [('radon-api.service', 'notify'), ('radon-research.service', 'simple'), ('radon-relay.service', 'simple')]
    states = {'radon-api.service': 'active', 'radon-research.service': 'inactive', 'radon-relay.service': 'inactive'}
    result, calls = _snapshot_replay(tmp_path, snapshot, states, 'resume_active_snapshot')
    assert result.returncode == 0, result.stderr
    assert not any('radon-research.service' in call for call in calls), calls
    assert '--no-block start radon-relay.service' in calls


def test_verify_restored_ignores_the_retired_watcher(tmp_path):
    snapshot = [('radon-research.service', 'simple'), ('radon-relay.service', 'simple')]
    states = {'radon-research.service': 'inactive', 'radon-relay.service': 'active'}
    (tmp_path / 'active.restored').write_text('')
    result, _ = _snapshot_replay(tmp_path, snapshot, states, 'verify_restored_state')
    assert result.returncode == 0, result.stderr


def test_verify_restored_still_requires_other_snapshotted_units(tmp_path):
    snapshot = [('radon-relay.service', 'simple')]
    states = {'radon-relay.service': 'inactive'}
    (tmp_path / 'active.restored').write_text('')
    result, _ = _snapshot_replay(tmp_path, snapshot, states, 'verify_restored_state')
    assert result.returncode == 67
