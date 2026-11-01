"""Model-only SSH permits inference UDS forwarding and read-only export SFTP."""
import importlib.util
from pathlib import Path
import pytest
CLOUD=Path(__file__).resolve().parents[1]

def module():
    spec=importlib.util.spec_from_file_location('model_transport',CLOUD/'scripts/model_transport.py')
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result);return result

def test_ssh_account_has_only_private_unix_forwarding_and_root_owned_export_read_scope():
    config=module().sshd_config()
    for line in ('Match User radon-model-tunnel','ChrootDirectory /var/lib/radon-model-export','ForceCommand internal-sftp -R -d /releases','AllowTcpForwarding local','PermitOpen [/run/radon-model-gateway/gateway.sock]:*','AllowStreamLocalForwarding local','AllowAgentForwarding no','X11Forwarding no','PermitTTY no','PermitTunnel no','PasswordAuthentication no','KbdInteractiveAuthentication no','PermitUserEnvironment no','PermitUserRC no'):
        assert line in config
    assert '/run/radon-model-control' not in config
    assert 'radon-subscription-vault' not in config

@pytest.mark.parametrize('key',['ssh-rsa AAAA','ssh-ed25519 broken','ssh-ed25519 AAAA\ncommand=evil','ssh-ed25519 '+('A'*68)])
def test_rejects_malformed_or_injectable_ssh_public_keys(key):
    api=module()
    with pytest.raises(ValueError):api.authorized_key(key)


def test_transport_config_updates_only_public_numeric_routing_fields():
    original='SOURCE_REFRESH_TOKEN=private\nRADON_MODEL_TUNNEL_UID=\nRADON_MODEL_TUNNEL_GID=\n'
    result=module().gateway_environment(original,2001,2002)
    assert 'SOURCE_REFRESH_TOKEN=private\n' in result
    assert 'RADON_MODEL_TUNNEL_UID=2001\n' in result
    assert 'RADON_MODEL_TUNNEL_GID=2002\n' in result
    assert result.count('RADON_MODEL_TUNNEL_UID=')==1


@pytest.mark.parametrize('gid',[0,1000])
def test_model_transport_rejects_reserved_group_aliases(gid):
    with pytest.raises(ValueError):module().gateway_environment('',2001,gid)


def test_environment_injection_is_disabled_in_global_sshd_scope():
    config = module().sshd_config()
    global_config, _, restricted = config.partition('Match User radon-model-tunnel')
    assert 'PermitUserEnvironment no' in global_config
    assert 'PermitUserEnvironment' not in restricted


def test_unix_forwarding_has_exact_non_network_permit_open():
    config = module().sshd_config()
    assert 'AllowTcpForwarding local' in config
    assert 'PermitOpen [/run/radon-model-gateway/gateway.sock]:*' in config
    assert 'PermitOpen any' not in config


def test_runtime_prepares_chroot_bind_only_for_configured_gateway():
    text = (CLOUD/'scripts/radon-app-runtime.sh').read_text()
    anchor = text.index('prepare_private_dir "$inference_ids" "$MODEL_GATEWAY_DIR"')
    hook = text.index('"$MODEL_TRANSPORT_HELPER" bind-inference', anchor)
    assert 'is_model_gateway "$unit" && [[ -n "$tunnel_uid" ]]' in text[anchor:hook]
    assert 'MODEL_TRANSPORT_HELPER=/usr/local/lib/radon/model_transport.py' in text


def _dir_info(mode, uid=0, gid=1000):
    import os, stat
    return os.stat_result((stat.S_IFDIR | mode, 0, 0, 2, uid, gid, 4096, 0, 0, 0))


def test_authorized_keys_live_outside_the_radon_writable_config_directory():
    # sshd StrictModes rejects keys under the canonical root:radon 1770 /etc/radon.
    api = module()
    assert not api.PRIVATE.is_relative_to(api.ENVIRONMENT.parent)
    assert f'AuthorizedKeysFile {api.PRIVATE}/authorized_keys' in api.sshd_config()


@pytest.mark.parametrize('mode', [0o1770, 0o1750])
def test_environment_parent_accepts_canonical_sticky_radon_config_directory(mode):
    module().config_parent(_dir_info(mode))


@pytest.mark.parametrize('info', [
    _dir_info(0o0770), _dir_info(0o1777), _dir_info(0o1770, uid=1000),
    _dir_info(0o1770, gid=1001), _dir_info(0o1770, gid=0),
])
def test_environment_parent_rejects_any_other_writable_config_directory(info):
    with pytest.raises(ValueError):module().config_parent(info)
