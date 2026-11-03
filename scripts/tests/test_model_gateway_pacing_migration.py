"""Cutover retains every known NVIDIA send, cooldown and trip deadline."""
import json
import os
from pathlib import Path
import pytest
from model_gateway import cache_migrate


def write(path,value):
    path.write_text(json.dumps(value));path.chmod(0o600);return path


def test_migration_merges_strictest_state_and_trip_sidecars_without_weakening_destination(tmp_path):
    first=write(tmp_path/'first.json',{'next_at':200,'cooldown_until':300,'consecutive_429':4,'last_429_at':100,'last_auth_failure':{'at':50,'status':401}})
    second=write(tmp_path/'second.json',{'next_at':150,'tripped_until':400,'last_auth_failure':{'at':80,'status':403}})
    Path(str(first)+'.tripped').write_text('500\n')
    destination=write(tmp_path/'nvidia-rate.json',{'next_at':250,'cooldown_until':350,'tripped_until':450,'consecutive_429':2})
    result=cache_migrate.migrate_pacing([first,second],destination)
    expected={'next_at':250,'cooldown_until':350,'consecutive_429':4,'last_429_at':100,'last_auth_failure':{'at':80,'status':403},'tripped_until':500}
    assert json.loads(destination.read_text())==expected
    assert float(Path(str(destination)+'.tripped').read_text())==500
    assert result['source_count']==2 and result['preserved_deadline']==500
    assert not destination.stat().st_mode & 0o077
    cache_migrate.migrate_pacing([first,second],destination)
    assert json.loads(destination.read_text())==expected


@pytest.mark.parametrize('value',[{'token':'must-not-copy'},{'next_at':-1},{'next_at':float('nan')},{'next_at':True},{'consecutive_429':1.5},{'last_auth_failure':{'at':5,'status':200}}])
def test_invalid_legacy_record_does_not_replace_existing_state(tmp_path,value):
    source=write(tmp_path/'legacy.json',value)
    destination=write(tmp_path/'nvidia-rate.json',{'tripped_until':500})
    before=destination.read_bytes()
    with pytest.raises((ValueError,OSError)):cache_migrate.migrate_pacing([source],destination)
    assert destination.read_bytes()==before


@pytest.mark.parametrize('linked',['source','destination'])
def test_pacing_migration_refuses_symlinks(tmp_path,linked):
    source=write(tmp_path/'source.json',{'next_at':200})
    destination=write(tmp_path/'nvidia-rate.json',{'tripped_until':500})
    target=source if linked=='source' else destination
    real=target.with_suffix('.real');target.rename(real);target.symlink_to(real)
    with pytest.raises((ValueError,OSError)):cache_migrate.migrate_pacing([source],destination)
    assert json.loads(real.read_text())==({'next_at':200} if linked=='source' else {'tripped_until':500})


def test_missing_pacing_sources_do_not_invent_or_reset_account_state(tmp_path):
    destination=tmp_path/'nvidia-rate.json'
    result=cache_migrate.migrate_pacing([tmp_path/'absent.json'],destination)
    assert result['source_count']==0 and not destination.exists()


def test_migration_entrypoint_uses_only_fixed_read_only_pacing_aliases():
    source=Path(cache_migrate.__file__).read_text()
    assert '/source/nvidia-pacing' in source
    assert '/cache/nvidia-rate.json' in source
    assert 'migrate_pacing(' in source.split('def main():',1)[1]


def test_existing_limiter_sidecars_with_default_umask_are_preserved_and_tightened(tmp_path):
    source=write(tmp_path/'source.json',{'next_at':100})
    destination=write(tmp_path/'nvidia-rate.json',{'tripped_until':200})
    marker=Path(str(destination)+'.tripped');marker.write_text('300\n');marker.chmod(0o644)
    lock=Path(str(destination)+'.lock');lock.write_text('');lock.chmod(0o644)
    cache_migrate.migrate_pacing([source],destination)
    assert json.loads(destination.read_text())['tripped_until']==300
    assert not marker.stat().st_mode & 0o077 and not lock.stat().st_mode & 0o077


def test_replayed_legacy_snapshot_does_not_revive_failures_after_gateway_success(tmp_path):
    source=write(tmp_path/'host.json',{'next_at':100,'consecutive_429':4})
    destination=tmp_path/'nvidia-rate.json'
    cache_migrate.migrate_pacing([source],destination)
    write(destination,{'next_at':600,'consecutive_429':0})
    result=cache_migrate.migrate_pacing([source],destination)
    assert json.loads(destination.read_text())=={'next_at':600,'consecutive_429':0}
    assert result['source_count']==0
    write(source,{'next_at':700,'consecutive_429':2})
    result=cache_migrate.migrate_pacing([source],destination)
    assert json.loads(destination.read_text())=={'next_at':700,'consecutive_429':2}
    assert result['source_count']==1
