"""PERF-073: options exposure overlaps the cube and EOD level GETs.

GET /options/exposure/{symbol} is uncached. The cube
(net-gex-by-expiration) and the EOD level overlay (gamma-levels) do not
depend on each other. Sequential GETs kept max in-flight at 1; overlapping
them raises it to 2 without a third call, a cache, or a partial cube.
"""
from __future__ import annotations

import threading
import time

import requests

from clients.menthorq_dashboard_client import MenthorQDashboardClient
from tests.test_menthorq_dashboard_client import (
    _Response,
    _exposure_payload,
    _jwt,
    _levels_payload,
)


def _mark(state: dict, lock: threading.Lock) -> None:
    with lock:
        state["in_flight"] += 1
        state["max_in_flight"] = max(state["max_in_flight"], state["in_flight"])
    try:
        time.sleep(0.05)
    finally:
        with lock:
            state["in_flight"] -= 1


class _OverlapSession:
    """Injected double. Not a requests.Session, so both legs share it."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.state = {"in_flight": 0, "max_in_flight": 0}
        self.calls: list[str] = []

    def get(self, url: str, **kwargs):
        with self.lock:
            self.calls.append(url)
        _mark(self.state, self.lock)
        if "gamma-levels" in url:
            return _Response(_levels_payload())
        return _Response(_exposure_payload())


def test_exposure_cube_and_levels_overlap_on_injected_client():
    session = _OverlapSession()
    client = MenthorQDashboardClient(access_token=_jwt(), http_session=session)

    result = client.fetch_exposure("MU", "eod")

    assert result["symbol"] == "MU"
    assert result["source"] == "menthorq_dashboard"
    assert len(session.calls) == 2
    assert any("net-gex-by-expiration/MU" in url for url in session.calls)
    assert any("gamma-levels/MU/eod" in url for url in session.calls)
    assert session.state["max_in_flight"] == 2, (
        f"max in-flight exposure GETs {session.state['max_in_flight']} (want 2)"
    )


def test_exposure_cube_and_levels_overlap_on_requests_session(monkeypatch):
    lock = threading.Lock()
    state = {"in_flight": 0, "max_in_flight": 0}
    calls: list[str] = []

    def get(self, url, **kwargs):
        with lock:
            calls.append(url)
        _mark(state, lock)
        if "gamma-levels" in url:
            return _Response(_levels_payload())
        return _Response(_exposure_payload())

    monkeypatch.setattr(requests.Session, "get", get)
    client = MenthorQDashboardClient(access_token=_jwt())

    result = client.fetch_exposure("MU", "eod")

    assert result["symbol"] == "MU"
    assert len(calls) == 2
    assert state["max_in_flight"] == 2, (
        f"max in-flight exposure GETs {state['max_in_flight']} (want 2)"
    )
