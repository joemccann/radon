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
