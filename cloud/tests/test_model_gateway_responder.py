"""Optional incident accounts cannot reinstall or authenticate model providers."""
import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace
import pytest
CLOUD=Path(__file__).resolve().parents[1]


def module():
    spec=importlib.util.spec_from_file_location('gateway_responder',CLOUD/'scripts/grok_responder_account.py')
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value


def test_scoped_responder_environment_uses_immutable_cli_and_private_inference_only():
    api=module()
    production='TURSO_DB_URL=libsql://fixture\nGROK_RESPONDER_TURSO_AUTH_TOKEN=fixture\nPUSHOVER_USER=fixture\nPUSHOVER_TOKEN=fixture\n'
    result=api.parse_env(api.derive_environment(production,'GROK_PAGE_RESPONDER=1\n'))
    assert result['GROK_BIN']=='/opt/radon-model-clients/current/bin/grok'
    assert result['RADON_MODEL_GATEWAY_SOCKET']=='/run/radon-model-gateway/gateway.sock'
    assert result['GROK_PAGE_RESPONDER']=='1'


def test_prepare_never_installs_native_clients_or_probes_local_grants(monkeypatch,tmp_path):
    api=module();calls=[]
    grant=tmp_path/'.grok/auth.json';grant.parent.mkdir();grant.write_text('{}');grant.chmod(0o600)
    monkeypatch.setattr(api,'ACCOUNT_HOME',tmp_path)
    monkeypatch.setattr(api,'pwd',SimpleNamespace(getpwnam=lambda _:SimpleNamespace(pw_uid=os.getuid())))
    monkeypatch.setattr(api,'as_account',lambda args,**kw:calls.append(list(map(str,args))) or SimpleNamespace(returncode=0,stdout=''))
    api.ensure_native_cli();api.verify_authentication()
    joined=' '.join(' '.join(row) for row in calls)
    assert 'managed_manifest' in joined and "request('status'" in joined
    for text in ('curl','install.sh','subscription_tokens','default_probe','auth.json','grok_upgrade','--seed-if-missing'):
        assert text not in joined


def test_optional_units_use_narrow_group_and_never_mutate_provider_clients():
    service=(CLOUD/'services/responder-domain/radon-grok-responder.service').read_text()
    upgrade=(CLOUD/'services/responder-domain/radon-grok-responder-upgrade.service').read_text()
    assert 'SupplementaryGroups=radon-model-connect' in service
    assert 'GROK_BIN=/opt/radon-model-clients/current/bin/grok' in service
    assert '-/run/radon-model-control' in service and '-/run/radon-subscription-vault' in service
    assert '-/var/lib/radon-responder/.grok/auth.json' in service
    assert 'grok_upgrade.py' not in upgrade and 'model_gateway/cli.py status' in upgrade
    assert module().NEW_TIMERS==('radon-grok-responder.timer',)


def test_model_peer_environment_preserves_secrets_and_existing_peers():
    api=module()
    original='SOME_REFRESH_TOKEN=private\nRADON_MODEL_TUNNEL_UID=2001\nRADON_MODEL_TUNNEL_GID=2002\nRADON_MODEL_CLIENT_UIDS=2003\n'
    result=api.derive_model_environment(original,2004,2002)
    assert result.count('RADON_MODEL_CLIENT_UIDS=')==1
    assert 'RADON_MODEL_CLIENT_UIDS=2003,2004\n' in result
    assert 'SOME_REFRESH_TOKEN=private\n' in result and 'RADON_MODEL_TUNNEL_UID=2001\n' in result
    for uid,gid in ((0,2002),(1000,2002),(2004,0),(2004,1000),(2004,2005)):
        with pytest.raises(api.MaintenanceError):api.derive_model_environment(original,uid,gid)


def test_runtime_rejects_routing_group_that_aliases_application_group():
    runtime=(CLOUD/'scripts/radon-app-runtime.sh').read_text()
    block=runtime.split('if [[ -n "$tunnel_gid" || -n "$tunnel_uid" || -n "$client_uids" ]]; then',1)[1].split('inference_ids=',1)[0]
    assert '"$tunnel_gid" != "${ids##*:}"' in block


def test_model_peer_admission_writes_only_public_routing_fields_and_restarts_only_gateway(monkeypatch,tmp_path):
    api=module();environment=tmp_path/'env'
    environment.write_text('PRIVATE_REFRESH_TOKEN=preserve\n');environment.chmod(0o640)
    monkeypatch.setattr(api,'PRODUCTION_ENV',environment)
    monkeypatch.setattr(api,'grp',SimpleNamespace(getgrnam=lambda name:SimpleNamespace(gr_gid=2002 if name==api.MODEL_GROUP else 2003)))
    monkeypatch.setattr(api.os,'getgrouplist',lambda *_:[32000,2003])
    monkeypatch.setattr(api,'read_root_file',lambda path:path.read_text())
    calls=[];writes=[]
    monkeypatch.setattr(api,'run',lambda command,**kw:calls.append(command))
    def write(path,raw,mode,**kwargs):
        writes.append((mode,kwargs));path.write_bytes(raw)
    monkeypatch.setattr(api,'atomic_root_file',write)
    api.admit_model_client(SimpleNamespace(pw_uid=32000,pw_gid=32000))
    assert 'PRIVATE_REFRESH_TOKEN=preserve\n' in environment.read_text()
    assert 'RADON_MODEL_CLIENT_UIDS=32000\n' in environment.read_text()
    assert writes==[(0o640,{'owner_gid':environment.stat().st_gid})]
    assert calls==[['/usr/sbin/usermod','-aG','radon-model-connect','radon-responder'],['/usr/bin/systemctl','try-restart','radon-model-gateway.service']]
    calls.clear();writes.clear()
    api.admit_model_client(SimpleNamespace(pw_uid=32000,pw_gid=32000))
    assert not writes and all(command[0]!='/usr/bin/systemctl' for command in calls)


@pytest.mark.parametrize('primary,extras',[(1000,[]),(32000,[0]),(32000,[1000])])
def test_model_peer_admission_refuses_privileged_groups_before_mutation(monkeypatch,primary,extras):
    api=module()
    monkeypatch.setattr(api,'grp',SimpleNamespace(getgrnam=lambda name:SimpleNamespace(gr_gid=2002 if name==api.MODEL_GROUP else 2003)))
    monkeypatch.setattr(api.os,'getgrouplist',lambda *_:[primary,*extras])
    monkeypatch.setattr(api,'run',lambda *a,**k:pytest.fail('no privilege mutation'))
    monkeypatch.setattr(api,'read_root_file',lambda *_:pytest.fail('refuse before reading production credentials'))
    with pytest.raises(api.MaintenanceError):api.admit_model_client(SimpleNamespace(pw_uid=32000,pw_gid=primary))


def test_default_responder_installer_uses_ci_receipt_without_download_login_or_probe():
    setup=(CLOUD/'scripts/setup-grok-page-responder.sh').read_text()
    assert 'managed_manifest' in setup and 'sudo -u radon' in setup
    for text in ('https://x.ai/cli/install.sh','--seed-if-missing','grok login --device-auth'):
        assert text not in setup


def test_default_responder_exec_pins_transport_after_legacy_environment_file():
    service=(CLOUD/'services/radon-grok-page-responder.service').read_text()
    line=next(row for row in service.splitlines() if row.startswith('ExecStart='))
    assert line=='ExecStart=/usr/bin/env GROK_BIN=/opt/radon-model-clients/current/bin/grok RADON_MODEL_GATEWAY_SOCKET=/run/radon-model-gateway/gateway.sock /home/radon/radon-page-responder/.venv/bin/python /home/radon/radon-page-responder/scripts/grok_page_responder.py'
    env=(CLOUD/'scripts/grok_responder_env.py').read_text()
    assert '("GROK_BIN", "/opt/radon-model-clients/current/bin/grok")' in env
    assert '("RADON_MODEL_GATEWAY_SOCKET", "/run/radon-model-gateway/gateway.sock")' in env
