"""The private inference gateway owns model credentials and executable lifecycle."""
from pathlib import Path
import hashlib
import re

import pytest

from test_app_runtime import CLOUD, TEST_SHA, _run, _run_line, _subscription_home

GATEWAY = "radon-model-gateway.service"
CONSUMERS = ("radon-api.service", "radon-nextjs.service", "radon-newsfeed.service", "radon-marketdesk.service", "radon-research.service", "radon-monitor.service")
SOCKET_DIR = "/run/radon-model-gateway"


@pytest.mark.parametrize("unit", CONSUMERS)
def test_model_consumers_have_only_the_private_gateway_socket(tmp_path, unit):
    home = _subscription_home(tmp_path)
    result = _run(tmp_path, ["run", unit], extra_env={"RADON_SUBSCRIPTION_HOME": str(home)})
    assert result.returncode == 0, result.stderr
    line = _run_line(result)
    assert f"{tmp_path / 'state/model-gateway'}:{SOCKET_DIR}:ro" in line
    assert f"--env RADON_MODEL_GATEWAY_SOCKET={SOCKET_DIR}/gateway.sock" in line
    assert "--env RADON_MODEL_GATEWAY_REQUIRED=1" in line
    for grant in (".grok", ".codex", ".claude", ".gemini", ".local/bin"):
        assert f"{home}/{grant}" not in line
    assert "RADON_SUBSCRIPTION_VAULT_SOCKET" not in line


def test_gateway_gets_model_grants_and_vault_socket_without_master_key_or_app_data(tmp_path):
    home = _subscription_home(tmp_path)
    result = _run(tmp_path, ["run", GATEWAY], extra_env={"RADON_SUBSCRIPTION_HOME": str(home)})
    assert result.returncode == 0, result.stderr
    line = _run_line(result)
    assert f"ghcr.io/joemccann/radon-model-gateway:{TEST_SHA} python -m scripts.model_gateway.server" in line
    for grant in (".grok", ".codex", ".gemini/antigravity-cli", ".gemini/config"):
        assert f"{home}/{grant}:/home/radon/{grant}:rw" in line
    assert f"{tmp_path / 'state/model-gateway'}:{SOCKET_DIR}:rw" in line
    assert "/run/radon-subscription-vault:rw" in line
    for forbidden in (".local/bin:", "CREDENTIALS_DIRECTORY", "secret_store", ":/home/radon/radon/data", "/var/lib/radon/media", "/var/lib/radon/ib-lease", "/run/radon-control"):
        assert forbidden not in line
    assert "--network host" in line


@pytest.mark.parametrize("unit", CONSUMERS)
def test_consumers_do_not_inherit_vendor_auth_environment(tmp_path, unit):
    source = tmp_path / "source.env"
    source.write_text("\n".join(("CLAUDE_CODE_OAUTH_TOKEN=forbidden", "CLAUDE_CODE_OAUTH_TOKEN_FILE=/forbidden", "NVIDIA_API_KEY=forbidden", "CEREBRAS_API_KEY=forbidden", "NVIDIA_TEXT_MODEL=forbidden", "GROK_AUTH_FILE=/forbidden", "CODEX_HOME=/forbidden", "GOOGLE_APPLICATION_CREDENTIALS=/forbidden", "CLOUDFLARE_API_TOKEN=forbidden", "TURSO_AUTH_TOKEN=app-database", "MARKETDESK_FIREBASE_REFRESH_TOKEN=source-login")) + "\n")
    result = _run(tmp_path, ["run", unit], extra_env={"RADON_TEST_ENV_FILE": str(source)})
    assert result.returncode == 0, result.stderr
    args = _run_line(result).split()
    rendered = Path(args[args.index("--env-file") + 1]).read_text()
    assert "forbidden" not in rendered
    assert "TURSO_AUTH_TOKEN=app-database" in rendered


def test_gateway_environment_is_model_only_and_uses_scoped_keeper_database_token(tmp_path):
    source = tmp_path / "source.env"
    source.write_text("\n".join(("CLAUDE_CODE_OAUTH_TOKEN=model-grant", "NVIDIA_API_KEY=nvidia-grant", "CEREBRAS_API_KEY=cerebras-grant", "CLOUDFLARE_API_TOKEN=workers-grant", "TURSO_DB_URL=libsql://example", "TURSO_AUTH_TOKEN=forbidden-operator-db", "TURSO_SUBSCRIPTION_KEEPER_AUTH_TOKEN=keeper-database", "PUSHOVER_USER=keeper-page", "PUSHOVER_TOKEN=keeper-page", "TWS_" "PASSWORD=forbidden", "CLERK_SECRET_KEY=forbidden", "DROPBOX_WRITE_REFRESH_TOKEN=forbidden", "MARKETDESK_FIREBASE_REFRESH_TOKEN=forbidden")) + "\n")
    result = _run(tmp_path, ["run", GATEWAY], extra_env={"RADON_TEST_ENV_FILE": str(source)})
    assert result.returncode == 0, result.stderr
    args = _run_line(result).split()
    rendered = Path(args[args.index("--env-file") + 1]).read_text()
    assert "forbidden" not in rendered
    assert "TURSO_AUTH_TOKEN=keeper-database" in rendered
    assert "TURSO_SUBSCRIPTION_KEEPER_AUTH_TOKEN=" not in rendered
    assert "NVIDIA_API_KEY=nvidia-grant" in rendered
    assert "CLOUDFLARE_API_TOKEN=workers-grant" in rendered


def test_gateway_and_keeper_ship_through_exact_release_manifest():
    for name in (GATEWAY, GATEWAY + ".d/runtime-container.conf", "radon-subscription-tokens.service"):
        path = CLOUD / "services" / name
        assert path.is_file()
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        assert f"{digest}  {name}" in (CLOUD / "config/installed-units.sha256").read_text()
    for helper in ("bootstrap-control-plane.sh", "deploy-root-helper.sh"):
        text = (CLOUD / "scripts" / helper).read_text()
        assert f"services/{GATEWAY}" in text
        assert f"/etc/systemd/system/{GATEWAY}" in text
        assert f"services/{GATEWAY}.d/runtime-container.conf" in text
    setup = (CLOUD / "scripts/setup-vps.sh").read_text()
    assert GATEWAY in re.search(r"readonly SERVICE_FILES=\(.*?\n\)", setup, re.S).group(0)


def test_keeper_calls_gateway_without_credentials_or_host_vendor_binaries():
    text = (CLOUD / "services/radon-subscription-tokens.service").read_text()
    assert "scripts.model_gateway.refresh_cli" in text
    assert "scripts.subscription_tokens --once" not in text
    assert "EnvironmentFile=" not in text
    assert "RADON_SUBSCRIPTION_VAULT_SOCKET" not in text
    assert "radon-model-gateway.service" in text
    assert "SuccessExitStatus=1" in text
    gateway = (CLOUD / "services" / GATEWAY).read_text()
    assert "LoadCredentialEncrypted" not in gateway
    assert "radon-subscription-vault.service" in gateway
    assert "Restart=always" in gateway


def test_gateway_is_in_deploy_readiness_and_core_restart_contract():
    deploy = (CLOUD / "scripts/deploy.sh").read_text()
    assert "radon-model-gateway" in re.search(r"^SERVICES=\(.*?\)", deploy, re.M).group(0)
    assert "check_model_gateway" in deploy
    assert "--unix-socket" in deploy
    helper = (CLOUD / "scripts/deploy-root-helper.sh").read_text()
    assert GATEWAY in re.search(r"readonly CORE_SERVICES=\(.*?\n\)", helper, re.S).group(0)


def test_gateway_readiness_uses_the_same_unprivileged_identity_as_socket_clients():
    unit = (CLOUD / 'services' / GATEWAY).read_text()
    assert 'User=radon' in unit
    assert 'ExecStartPost=/usr/bin/curl' in unit
    dropin = (CLOUD / 'services' / (GATEWAY + '.d/runtime-container.conf')).read_text()
    assert 'User=root' in dropin
    assert 'ExecStartPost=\nExecStartPost=' in dropin
    line = next(line for line in dropin.splitlines() if line.startswith('ExecStartPost=') and line != 'ExecStartPost=')
    # The runtime drop-in runs the engine as root; the server accepts uid1000
    # peers, so a plain root curl would keep an otherwise healthy unit failed.
    assert '/usr/bin/setpriv --reuid=radon --regid=radon --clear-groups /usr/bin/curl' in line


@pytest.mark.parametrize('unit', CONSUMERS)
def test_only_privileged_api_consumer_mounts_management_socket(tmp_path,unit):
    result=_run(tmp_path,['run',unit])
    assert result.returncode==0,result.stderr
    line=_run_line(result)
    if unit=='radon-api.service':
        assert f"{tmp_path/'state/model-control'}:/run/radon-model-control:ro" in line
        assert '--env RADON_MODEL_CONTROL_SOCKET=/run/radon-model-control/control.sock' in line
    else:
        assert '/run/radon-model-control' not in line


def test_optional_tunnel_group_widens_only_inference_parent(tmp_path):
    source=tmp_path/'source.env';source.write_text('RADON_MODEL_TUNNEL_UID=2001\nRADON_MODEL_TUNNEL_GID=2002\n')
    helper=tmp_path/'model-transport.py'
    marker=tmp_path/'bound'
    helper.write_text('import pathlib,sys\nassert sys.argv[1:]==["bind-inference"]\npathlib.Path('+repr(str(marker))+').write_text("bound")\n')
    result=_run(tmp_path,['run',GATEWAY],extra_env={'RADON_TEST_ENV_FILE':str(source),'RADON_TEST_MODEL_TRANSPORT_HELPER':str(helper)})
    assert result.returncode==0,result.stderr
    assert marker.read_text()=='bound'
    assert '--group-add 2002' in _run_line(result)
    import stat
    assert stat.S_IMODE((tmp_path/'state/model-gateway').stat().st_mode)==0o710
    assert stat.S_IMODE((tmp_path/'state/model-control').stat().st_mode)==0o700
    assert stat.S_IMODE((tmp_path/'state/subscription-vault').stat().st_mode)==0o700


@pytest.mark.parametrize('uid,gid',[('2001',''),('not-a-uid','2002'),('0','2002')])
def test_optional_tunnel_identity_requires_valid_numeric_pair(tmp_path,uid,gid):
    source=tmp_path/'source.env';source.write_text(f'RADON_MODEL_TUNNEL_UID={uid}\nRADON_MODEL_TUNNEL_GID={gid}\n')
    assert _run(tmp_path,['run',GATEWAY],extra_env={'RADON_TEST_ENV_FILE':str(source)}).returncode==78


def test_optional_extra_client_uid_uses_same_narrow_inference_group(tmp_path):
    source=tmp_path/'source.env';source.write_text('RADON_MODEL_CLIENT_UIDS=996\nRADON_MODEL_TUNNEL_GID=2002\n')
    result=_run(tmp_path,['run',GATEWAY],extra_env={'RADON_TEST_ENV_FILE':str(source)})
    assert result.returncode==0,result.stderr
    assert '--group-add 2002' in _run_line(result)
    args=_run_line(result).split()
    assert 'RADON_MODEL_CLIENT_UIDS=996' in Path(args[args.index('--env-file')+1]).read_text()


def test_gateway_paces_in_persistent_cache_and_retains_operator_limits(tmp_path):
    source=tmp_path/'source.env'
    limits={'RADON_NVIDIA_RPM':'7','RADON_NVIDIA_TRIP_AFTER_429S':'2','RADON_NVIDIA_TRIP_SECS':'3600','RADON_NVIDIA_AUTH_BLOCK_SECS':'7200','RADON_NVIDIA_LADDER_MAX_WAIT_S':'3'}
    source.write_text('RADON_NVIDIA_RATE_STATE=/legacy/account-state.json\n'+''.join(key+'='+value+'\n' for key,value in limits.items()))
    result=_run(tmp_path,['run',GATEWAY],extra_env={'RADON_TEST_ENV_FILE':str(source)})
    assert result.returncode==0,result.stderr
    line=_run_line(result);args=line.split();rendered=Path(args[args.index('--env-file')+1]).read_text()
    assert '--env RADON_NVIDIA_RATE_STATE=/var/lib/radon/model-cache/nvidia-rate.json' in line
    assert 'RADON_NVIDIA_RATE_STATE=' not in rendered
    for key,value in limits.items():assert key+'='+value+'\n' in rendered
    repeated=_run(tmp_path,['run',GATEWAY],extra_env={'RADON_TEST_ENV_FILE':str(source)})
    assert repeated.returncode==0,repeated.stderr
    assert '/var/lib/radon/model-cache/nvidia-rate.json' in _run_line(repeated)


def test_tunnel_bind_failure_prevents_gateway_container_start(tmp_path):
    source=tmp_path/'source.env';source.write_text('RADON_MODEL_TUNNEL_UID=2001\nRADON_MODEL_TUNNEL_GID=2002\n')
    helper=tmp_path/'model-transport.py';helper.write_text('raise SystemExit(78)\n')
    result=_run(tmp_path,['run',GATEWAY],extra_env={'RADON_TEST_ENV_FILE':str(source),'RADON_TEST_MODEL_TRANSPORT_HELPER':str(helper)})
    assert result.returncode==78
    assert 'ghcr.io/joemccann/radon-model-gateway:' not in result.stdout
