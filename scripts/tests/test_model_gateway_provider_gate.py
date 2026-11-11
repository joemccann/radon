"""Provider admission: bounded shared inference slots, exclusive credential renewal."""
from __future__ import annotations

import threading
import time

import pytest

from model_gateway.provider_gate import ProviderGate, provider_concurrency


def test_capacity_one_keeps_the_single_in_flight_contract():
    gate = ProviderGate(1)
    assert gate.acquire(timeout=0.01)
    assert not gate.acquire(timeout=0.01)
    gate.release()
    assert gate.acquire(timeout=0.01)
    gate.release()


def test_capacity_admits_that_many_concurrent_calls_and_no_more():
    gate = ProviderGate(3)
    assert all(gate.acquire(timeout=0.01) for _ in range(3))
    assert not gate.acquire(timeout=0.01)
    gate.release()
    assert gate.acquire(timeout=0.01)
    for _ in range(3):
        gate.release()


def test_release_without_acquire_is_an_error():
    with pytest.raises(RuntimeError):
        ProviderGate(2).release()


def test_renewal_waits_for_in_flight_calls_and_blocks_new_ones():
    gate = ProviderGate(4)
    assert gate.acquire(timeout=0.01)
    entered = threading.Event()
    leave = threading.Event()

    def renew():
        with gate.exclusive():
            entered.set()
            assert leave.wait(2)

    worker = threading.Thread(target=renew)
    worker.start()
    time.sleep(0.05)
    assert not entered.is_set(), "renewal must not overlap an in-flight call"
    # A waiting renewal holds new calls back so it cannot starve.
    assert not gate.acquire(timeout=0.05)
    gate.release()
    assert entered.wait(1)
    assert not gate.acquire(timeout=0.05), "no call may run during renewal"
    leave.set()
    worker.join(2)
    assert gate.acquire(timeout=0.5)
    gate.release()


def test_engine_runs_concurrent_calls_on_one_provider_up_to_capacity():
    from model_gateway import engine

    gate = ProviderGate(2)
    inside = threading.Barrier(2, timeout=2)
    results = []

    def call(name, auth, model):
        inside.wait()  # both calls must be inside the provider at once
        return 200, "", {"ok": True}

    def one():
        with engine.attempt_scope(deadline=time.monotonic() + 10, seen=set(), locks={"anthropic": gate}):
            results.append(engine._invoke_provider(call, "anthropic", None, "m"))

    threads = [threading.Thread(target=one) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)
    assert len(results) == 2


@pytest.mark.parametrize("raw,expected", [(None, 1), ("", 1), ("1", 1), ("6", 6), ("16", 16)])
def test_concurrency_reads_the_gateway_environment(raw, expected):
    env = {} if raw is None else {"RADON_MODEL_PROVIDER_CONCURRENCY": raw}
    assert provider_concurrency(env) == expected


@pytest.mark.parametrize("raw", ["0", "17", "-1", "2.5", "six", " 4"])
def test_invalid_concurrency_refuses_to_start(raw):
    with pytest.raises(ValueError):
        provider_concurrency({"RADON_MODEL_PROVIDER_CONCURRENCY": raw})


def test_gateway_builds_provider_gates_at_the_configured_capacity(monkeypatch):
    from model_gateway.server import Gateway

    monkeypatch.setenv("RADON_MODEL_PROVIDER_CONCURRENCY", "3")
    gate = Gateway().provider_locks["anthropic"]
    assert isinstance(gate, ProviderGate) and gate.capacity == 3
