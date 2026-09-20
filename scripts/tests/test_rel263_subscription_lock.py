"""REL-263: an unavailable serialization lock cannot authorize token work."""
import errno
from pathlib import Path
from unittest.mock import Mock
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_subscription_tokens import make_runtime, write_doc, grok_doc, NOW
from scripts import subscription_tokens as st


@pytest.mark.parametrize('mode', ['once', 'seal', 'restore', 'reauth'])
@pytest.mark.parametrize('site,code', [
    ('mkdir', errno.EACCES), ('open', errno.EACCES),
    ('open', errno.ENOENT), ('open', errno.EIO), ('flock', errno.EIO),
])
def test_unavailable_lock_refuses_all_credential_work(tmp_path, monkeypatch, mode, site, code):
    heartbeat = Mock()
    rt = make_runtime(tmp_path, heartbeat=heartbeat)
    write_doc(rt, 'grok', grok_doc(NOW))
    credential = st.PROVIDERS['grok'].path(rt.env)
    before = credential.read_bytes()
    work = Mock(return_value={'exit_code': 0})
    monkeypatch.setattr(st, '_run_locked', work)

    def fail(*args, **kwargs):
        raise OSError(code, 'synthetic lock failure')

    if site == 'mkdir':
        monkeypatch.setattr(Path, 'mkdir', fail)
    elif site == 'open':
        monkeypatch.setattr(st.os, 'open', fail)
    else:
        monkeypatch.setattr(st.fcntl, 'flock', fail)

    report = st.run(mode, ['grok'], rt, json_output=True)
    assert report['exit_code'] == st.EXIT_CONFIG
    assert report['error'] == 'subscription refresh lock unavailable'
    assert 'skipped' not in report
    work.assert_not_called()
    assert credential.read_bytes() == before
    assert rt.http.calls == []
    assert rt.vault.seals == []
    assert not rt.sidecar_path.exists()
    assert rt.sent == []
    assert heartbeat.call_args.args[:2] == (st.SERVICE_NAME, 'error')


def test_held_lock_skips_and_released_lock_allows_work(tmp_path, monkeypatch):
    rt = make_runtime(tmp_path)
    work = Mock(return_value={'exit_code': 0})
    monkeypatch.setattr(st, '_run_locked', work)
    with st.run_lock(rt.lock_path) as acquired:
        assert acquired
        assert st.run('once', ['grok'], rt)['skipped']
        work.assert_not_called()
    assert st.run('once', ['grok'], rt)['exit_code'] == 0
    work.assert_called_once()
