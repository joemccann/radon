"""The timer triggers gateway-owned credential renewal without provider access."""
from unittest.mock import Mock

import pytest


def _module():
    from scripts.model_gateway import refresh_cli
    return refresh_cli


def test_refresh_timer_submits_then_polls_to_the_keeper_exit_code():
    rpc = Mock(side_effect=[{"job_id": "job-1", "status": "queued"}, {"status": "running"}, {"status": "complete", "exit_code": 1}])
    sleep = Mock()
    result = _module().main([], request=rpc, sleep=sleep)
    assert result == 1
    assert rpc.call_args_list[0].args == ("refresh", {"mode": "once"})
    assert rpc.call_args_list[1].args == ("refresh-status", {"job_id": "job-1"})
    sleep.assert_called_once_with(2.0)


@pytest.mark.parametrize("exit_code", [0, 1, 78])
def test_refresh_preserves_only_expected_lifecycle_exit_codes(exit_code):
    rpc = Mock(side_effect=[{"job_id": "job-1", "status": "queued"}, {"status": "complete", "exit_code": exit_code}])
    assert _module().main([], request=rpc) == exit_code


@pytest.mark.parametrize("reply", [{}, {"job_id": ""}, {"job_id": "../bad"}, {"job_id": "x" * 257}])
def test_refresh_rejects_malformed_job_ids(reply):
    rpc = Mock(return_value=reply)
    assert _module().main([], request=rpc) == 78
    assert rpc.call_count == 1


def test_refresh_rpc_failure_is_a_real_unit_failure():
    rpc = Mock(side_effect=RuntimeError("provider secret must not be printed"))
    assert _module().main([], request=rpc) == 78


def test_refresh_deadline_does_not_wait_forever_or_claim_success():
    rpc = Mock(side_effect=[{"job_id": "job-1", "status": "queued"}, {"status": "running"}])
    clock = Mock(side_effect=[0.0, 0.0, 1601.0])
    assert _module().main([], request=rpc, monotonic=clock, sleep=Mock()) == 78
    assert rpc.call_count == 2


@pytest.mark.parametrize("reply", [{"status": "complete", "exit_code": True}, {"status": "complete", "exit_code": 143}, {"status": "failed"}, {"status": "unexpected"}])
def test_refresh_never_turns_unknown_or_failed_jobs_into_success(reply):
    rpc = Mock(side_effect=[{"job_id": "job-1", "status": "queued"}, reply])
    assert _module().main([], request=rpc) == 78


def test_timer_payload_is_accepted_by_the_real_lifecycle_manager(monkeypatch):
    import sys
    from types import SimpleNamespace
    from scripts.model_gateway import lifecycle
    providers = ('claude', 'codex', 'grok', 'antigravity')
    monkeypatch.setitem(sys.modules, 'subscription_tokens', SimpleNamespace(PROVIDERS=providers))
    scheduled = []
    class Thread:
        def __init__(self, *, target, args, daemon):
            scheduled.append(args)
        def start(self):
            pass
    monkeypatch.setattr(lifecycle.threading, 'Thread', Thread)
    manager = lifecycle.RefreshManager({})
    def rpc(operation, payload, **kwargs):
        if operation == 'refresh':
            return manager.start(payload)
        manager.jobs[payload['job_id']].update(status='complete', exit_code=0)
        return manager.status(payload)
    assert _module().main([], request=rpc) == 0
    assert scheduled[0][2] == list(providers)
