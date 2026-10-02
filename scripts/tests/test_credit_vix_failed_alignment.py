"""REL-297 / R-716: successful transports need a usable three-leg sample."""
import copy

import pytest

import fetch_credit_vix as mod

ROW = {'date': '2026-09-28', 'shy_close': 80.0, 'hyg_close': 75.0,
       'vix_close': 20.0, 'spread': 5.0}


@pytest.fixture
def cycle(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(mod, 'CREDIT_VIX_JSON', tmp_path / 'credit.json')
    monkeypatch.setattr(mod, 'load_cached_series', lambda **kw: [copy.deepcopy(ROW)])
    monkeypatch.setattr(mod.writer, 'ensure_no_replica_for_writers', lambda: None)
    monkeypatch.setattr(mod.writer, 'upsert_credit_vix_rows',
                        lambda *a, **k: calls.append(('rows', a)))
    monkeypatch.setattr(mod.writer, 'upsert_scan_snapshot',
                        lambda *a, **k: calls.append(('snapshot', a)))
    monkeypatch.setattr(mod.writer, 'record_service_health',
                        lambda service, state, **k: calls.append(('health', state)))
    monkeypatch.setattr(mod, 'fetch_equity_closes', lambda *a, **k: (
        {'SHY': {'2026-09-30': 80.0}, 'HYG': {'2026-09-30': 75.0}},
        'ib', {'SHY': 'ib', 'HYG': 'ib'}))
    monkeypatch.setattr(mod, 'fetch_vix_closes', lambda **k: ({'2026-09-29': 20.0}, 'ib'))
    return calls


def test_disjoint_sources_cannot_report_healthy_fresh_data(cycle):
    payload = mod.run()
    assert ('health', 'error') in cycle
    assert ('health', 'ok') not in cycle
    assert payload['status'] == 'stale_source'
    assert payload['missing'] is True
    assert payload['series'] == [ROW]
    assert not any(kind == 'rows' for kind, _ in cycle)


def test_disjoint_sources_without_cache_fail_the_cycle(cycle, monkeypatch):
    monkeypatch.setattr(mod, 'load_cached_series', lambda **k: [])
    with pytest.raises(RuntimeError):
        mod.run()
    assert ('health', 'error') in cycle
    assert not any(kind in ('rows', 'snapshot') for kind, _ in cycle)


def test_no_db_alignment_failure_keeps_all_writers_unreached(cycle):
    payload = mod.run(no_db=True)
    assert payload.get('missing') is True
    assert cycle == []


def test_complete_source_outage_is_marked_missing_for_existing_consumers(cycle, monkeypatch):
    monkeypatch.setattr(mod, 'fetch_equity_closes', lambda *a, **k: ({}, 'none', {}))
    monkeypatch.setattr(mod, 'fetch_vix_closes', lambda **k: ({}, 'none'))
    payload = mod.run()
    assert payload.get('missing') is True
    assert payload['status'] == 'stale_source'
    assert ('health', 'error') in cycle


def test_unchanged_aligned_session_is_still_a_healthy_weekend_run(cycle, monkeypatch):
    monkeypatch.setattr(mod, 'fetch_equity_closes', lambda *a, **k: (
        {'SHY': {ROW['date']: 80.0}, 'HYG': {ROW['date']: 75.0}},
        'ib', {'SHY': 'ib', 'HYG': 'ib'}))
    monkeypatch.setattr(mod, 'fetch_vix_closes', lambda **k: ({ROW['date']: 20.0}, 'ib'))
    payload = mod.run()
    assert not payload.get('missing')
    assert payload['source'] == 'ib'
    assert ('health', 'ok') in cycle
    assert not any(kind == 'rows' for kind, _ in cycle)
