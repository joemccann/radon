"""PERF-022: ivrank UW GETs must share one UWClient session.

Tonight's delta replaced raw urlopen with UWClient, but `_uw_get` still
constructed a new client (new requests.Session, TCP, TLS) on every call.
A nightly run that falls through to UW and repairs the 25-lookup bound
opens 27 sessions. Sequential GETs on one job must reuse one session
the way internals skew does (PERF-014).
"""
from __future__ import annotations

import sys

import pytest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


class _CountingUWClient:
    inits = 0
    gets = 0

    def __init__(self, *args, **kwargs):
        _CountingUWClient.inits += 1

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def close(self):
        return None

    def get(self, endpoint, params=None, **kwargs):
        _CountingUWClient.gets += 1
        date = (params or {}).get("date") or "2026-10-20"
        if endpoint.rstrip("/").endswith("volatility/stats"):
            return {"data": {"date": date, "iv_rank": 12.5}}
        return {"data": [{"date": date, "volatility": "0.15"}]}


class _PreviousJobClient:
    closed = False

    def close(self):
        self.closed = True

    def get(self, *args, **kwargs):
        raise AssertionError("cached previous job leaked into isolated session budget")


@pytest.mark.parametrize("warm_cache", [False, True], ids=["cold-cache", "warm-cache"])
def test_ivrank_five_uw_gets_open_one_client(monkeypatch, warm_cache):
    import clients.uw_client as uw_mod
    import fetch_ivrank as iv

    stale = _PreviousJobClient() if warm_cache else None
    if stale is not None:
        iv.close_uw_session()
        iv._uw_session = stale

    # This budget measures a fresh job, regardless of earlier test jobs.
    # Replacing the constructor cannot replace an already cached client.
    iv.close_uw_session()
    _CountingUWClient.inits = 0
    _CountingUWClient.gets = 0
    monkeypatch.setenv("UW_TOKEN", "synthetic-test-token")
    monkeypatch.setattr(uw_mod, "UWClient", _CountingUWClient)
    monkeypatch.setattr(iv, "writer", None)

    try:
        assert iv._real_uw_fetch() == [
            {"date": "2026-10-20", "iv": 0.15, "source": "uw"}
        ]
        assert iv._real_uw_iv_for_date("2026-10-16") == 0.15
        assert iv._real_uw_iv_for_date("2026-10-17") == 0.15
        assert iv._real_uw_iv_for_date("2026-10-20") == 0.15
        assert iv._real_uw_check() == {"date": "2026-10-20", "iv_rank": 12.5}
        assert stale is None or stale.closed
        assert _CountingUWClient.gets == 5
        assert _CountingUWClient.inits == 1, (
            f"UWClient constructions {_CountingUWClient.inits} "
            "(want 1 session for 5 sequential ivrank GETs, not one per GET)"
        )
    finally:
        closer = getattr(iv, "close_uw_session", None)
        if closer is not None:
            closer()
