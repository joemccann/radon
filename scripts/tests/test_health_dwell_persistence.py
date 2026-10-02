"""REL-167 / R-468: restarts must not reset an observed dependency outage."""
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo
import json

import pytest
from health_service import probes, serve

AT = datetime(2026, 10, 2, 12, tzinfo=ZoneInfo('America/New_York'))


def test_unit_outage_survives_restart_and_recovery_clears_it(tmp_path, monkeypatch):
    path = tmp_path / 'dwell.json'
    clock = [1000.0]
    active = ['failed']
    monkeypatch.setattr(serve.time, 'time', lambda: clock[0])
    monkeypatch.setattr(serve.subprocess, 'run', lambda *a, **k: SimpleNamespace(
        returncode=0, stdout=f'Id=radon-monitor.service\nActiveState={active[0]}\nSubState=failed\nResult=exit-code\n'))
    def cache():
        return serve.UnitStateCache(['radon-monitor.service'], dwell_store=serve.DwellStore(path))
    first = cache()
    first.refresh_once()
    clock[0] += 1000
    restored = cache()
    restored.refresh_once()
    units, age = restored.snapshot()
    assert units['radon-monitor.service']['non_up_secs'] == 1000
    assert probes.aggregate_state({'radon-api': {'state': 'up'}}, units,
                                  units_age_secs=age, host_role='combined', now_et=AT) == 'down'
    active[0] = 'active'
    restored.refresh_once()
    active[0] = 'failed'
    reset = cache()
    reset.refresh_once()
    assert reset.snapshot()[0]['radon-monitor.service']['non_up_secs'] == 0


@pytest.mark.parametrize('nested', [False, True])
def test_probe_and_auth_dwell_survive_restart_with_app_policy(tmp_path, monkeypatch, nested):
    clock = [1000.0]
    monkeypatch.setattr(serve.time, 'time', lambda: clock[0])
    def fetch():
        if not nested:
            return {'ib-gateway': {'state': 'down'}}
        return {'radon-api': {'state': 'up', 'payload': {
            'status': 'ok', 'service_state': 'reachable', 'auth_state': 'awaiting_2fa',
            'upstream_dead': False, 'port_listening': True}}}
    path = tmp_path / 'dwell.json'
    initial = serve.ProbeCache(fetch_fn=fetch, dwell_store=serve.DwellStore(path))
    initial.refresh_once()
    for seconds, expected in [(899, 'degraded'), (901, 'down')]:
        clock[0] = 1000 + seconds
        restored = serve.ProbeCache(fetch_fn=fetch, dwell_store=serve.DwellStore(path))
        restored.refresh_once()
        values, age = restored.snapshot()
        assert probes.aggregate_state(values, {}, units_age_secs=0, probes_age_secs=age,
                                      host_role='combined', now_et=AT) == expected
        if nested:
            assert probes.aggregate_state(values, {}, units_age_secs=0, probes_age_secs=age,
                                          host_role='app', now_et=AT) == 'degraded'


def test_failed_state_replace_preserves_prior_bytes_and_retries(tmp_path, monkeypatch, caplog):
    path = tmp_path / 'dwell.json'
    store = serve.DwellStore(path)
    store.stamp('unit', {'worker': 'down'}, 1000)
    before = path.read_bytes()
    replace = serve.os.replace
    def fail(*args):
        raise OSError('fixture disk full')
    monkeypatch.setattr(serve.os, 'replace', fail)
    assert store.stamp('unit', {'worker': 'up'}, 2000) == {'worker': None}
    assert path.read_bytes() == before
    assert 'dwell' in caplog.text.lower()
    monkeypatch.setattr(serve.os, 'replace', replace)
    store.stamp('unit', {'worker': 'up'}, 2001)
    assert serve.DwellStore(path).stamp('unit', {'worker': 'down'}, 3000) == {'worker': 0}


@pytest.mark.parametrize('raw', [b'not json', b'[]', b'\xff', b'x' * 65537])
def test_invalid_state_is_visible_and_does_not_invent_old_dwell(tmp_path, raw, caplog):
    path = tmp_path / 'dwell.json'
    path.write_bytes(raw)
    store = serve.DwellStore(path)
    assert 'could not be restored' in caplog.text
    assert store.stamp('unit', {'worker': 'down'}, 1000) == {'worker': 0}


def test_namespace_updates_preserve_other_cache_and_rebase_future_clock(tmp_path):
    path = tmp_path / 'dwell.json'
    path.write_text(json.dumps({'unit:worker': 2000, 'probe:ib-gateway': 500,
                                'unit:invalid': True, 'unit:nonfinite': float('nan')}))
    store = serve.DwellStore(path)
    assert store.stamp('unit', {'worker': 'down'}, 1000) == {'worker': 0}
    assert serve.DwellStore(path).stamp('probe', {'ib-gateway': 'down'}, 1000) == {'ib-gateway': 500}
    assert store.stamp('unit', {}, 1000) == {}
    assert json.loads(path.read_text()) == {'probe:ib-gateway': 500}


@pytest.mark.parametrize('probe,age', [({'state': 'unknown', 'non_up_secs': 1000}, 0),
                                    ({'state': 'down', 'non_up_secs': 1000}, 999)])
def test_unproven_or_stale_broker_evidence_cannot_escalate(probe, age):
    assert probes.aggregate_state({'ib-gateway': probe}, {}, units_age_secs=0,
                                  probes_age_secs=age, now_et=AT, host_role='combined') == 'unknown'
