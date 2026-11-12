"""Provisional current-session rows for the daily-close Cboe indicators.

VIX TS and Panic sample every 5 minutes in ET trading hours. Each run builds
today's row from one live source (utils/index_live.py) and writes it to a
small ``<service>:live`` snapshot. The official snapshot is not rewritten on
an unchanged-source live tick unless it is over 12 hours old.
"""
from __future__ import annotations

import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))

import fetch_panic_index  # noqa: E402
import fetch_vixts  # noqa: E402
from utils import index_live  # noqa: E402

# Monday 2026-10-19 15:00 ET; the last completed session is Friday 2026-10-16.
NOW = datetime(2026, 10, 19, 19, 0, tzinfo=timezone.utc)


def _live(values, day="2026-10-19", source="cboe_delayed"):
    return {"date": day, "values": values, "source": source, "quote_time": f"{day}T14:45:00"}


class _Recorder:
    def __init__(self):
        self.snapshots: list[tuple] = []
        self.health: list[tuple] = []
        self.row_upserts = 0

    def ensure_no_replica_for_writers(self):
        pass

    def upsert_scan_snapshot(self, service, scan_time, payload):
        self.snapshots.append((service, scan_time, payload))

    def record_service_health(self, service, state, **kwargs):
        self.health.append((service, state))

    def upsert_vixts_rows(self, *a, **k):
        self.row_upserts += 1

    def upsert_panic_index_rows(self, *a, **k):
        self.row_upserts += 1

    def services(self):
        return [s[0] for s in self.snapshots]


# ── utils/index_live ──────────────────────────────────────────────


def _quote(price, when):
    response = Mock()
    response.json = Mock(return_value={"data": {"current_price": price, "last_trade_time": when}})
    return response


def test_cboe_live_requires_every_symbol_on_one_session(monkeypatch):
    import requests

    quotes = {"VIX": _quote(17.6, "2026-11-11T15:44:32"), "SKEW": _quote(149.09, "2026-11-11T15:44:00")}
    get = Mock(side_effect=lambda url, **k: quotes[url.rsplit("_", 1)[1].removesuffix(".json")])
    monkeypatch.setattr(requests, "get", get)
    assert index_live.live_from_cboe(("VIX", "SKEW")) == {
        "date": "2026-11-11", "values": {"VIX": 17.6, "SKEW": 149.09},
        "source": "cboe_delayed", "quote_time": "2026-11-11T15:44:32",
    }
    assert get.call_args_list[1].args[0] == "https://cdn-api.cboe.com/api/global/delayed_quotes/quotes/_SKEW.json"
    quotes["SKEW"] = _quote(149.09, "2026-11-10T17:00:19")
    assert index_live.live_from_cboe(("VIX", "SKEW")) is None
    for bad in (0, -1, float("nan"), True, "x", None):
        quotes["SKEW"] = _quote(bad, "2026-11-11T15:44:00")
        assert index_live.live_from_cboe(("VIX", "SKEW")) is None


def test_ib_live_needs_todays_bar_for_every_symbol(monkeypatch):
    import requests

    def post(url, **kwargs):
        assert url == "http://127.0.0.1:8321/historical/bars"
        response = Mock()
        response.json = Mock(return_value={"bars": [{"date": "2026-10-19", "close": 20.0}]})
        return response

    monkeypatch.setattr(requests, "post", post)
    assert index_live.live_from_ib(("VIX", "VIX3M"), NOW)["values"] == {"VIX": 20.0, "VIX3M": 20.0}
    sunday = datetime(2026, 10, 19, 3, 0, tzinfo=timezone.utc)  # 23:00 ET Sunday
    assert index_live.live_from_ib(("VIX",), sunday) is None


def test_fetch_live_prefers_ib_then_cboe_and_never_raises():
    ib = Mock(return_value=_live({"VIX": 1.0}, source="ib"))
    cboe = Mock(return_value=_live({"VIX": 2.0}))
    assert index_live.fetch_live(("VIX",), NOW, try_ib=True, label="t", ib=ib, cboe=cboe)["source"] == "ib"
    cboe.assert_not_called()
    assert index_live.fetch_live(("VIX",), NOW, try_ib=False, label="t", ib=ib, cboe=cboe)["source"] == "cboe_delayed"
    ib.side_effect = RuntimeError("pool down")
    cboe.side_effect = RuntimeError("cdn down")
    assert index_live.fetch_live(("VIX",), NOW, try_ib=True, label="t", ib=ib, cboe=cboe) is None


@pytest.mark.parametrize("day, official, ok", [
    ("2026-10-19", "2026-10-16", True),
    ("2026-10-16", "2026-10-16", False),   # not after the official close
    ("2026-10-20", "2026-10-16", False),   # future session
    ("2026-10-19", None, False),
])
def test_is_newer(day, official, ok):
    assert index_live.is_newer(_live({}, day=day), official, NOW) is ok


def test_snapshot_age():
    assert index_live.snapshot_is_old("2026-10-19T06:59:00Z", NOW) is True
    assert index_live.snapshot_is_old("2026-10-19T07:01:00Z", NOW) is False
    assert index_live.snapshot_is_old(None, NOW) is True


# ── VIX TS ────────────────────────────────────────────────────────


def _vixts_cached(scan_time):
    row = {"date": "2026-10-16", "vix": 20.0, "vix3m": 21.0, "ratio": 0.9524, "spx": 6600.0}
    return {"scan_time": scan_time, "source_last_modified": {}, "data_date": "2026-10-16",
            "count": 1, "current": {**row, "regime": "FLAT"}, "stats": {}, "series": [row]}


@pytest.fixture
def vixts(monkeypatch, tmp_path):
    rec = _Recorder()
    monkeypatch.setattr(fetch_vixts, "writer", rec)
    monkeypatch.setattr(fetch_vixts, "VIXTS_JSON", tmp_path / "vixts.json")
    monkeypatch.setattr(fetch_vixts, "_fetch_all", lambda *a, **k: (dict.fromkeys(fetch_vixts._SYMBOLS), {}))
    return rec


def test_vixts_live_tick_writes_only_the_live_snapshot_and_a_heartbeat(vixts, monkeypatch):
    monkeypatch.setattr(fetch_vixts, "_read_json_cache", lambda: _vixts_cached("2026-10-19T13:00:00Z"))
    monkeypatch.setattr(fetch_vixts, "_fetch_live", lambda now: _live({"VIX": 18.0, "VIX3M": 20.0, "SPX": 6700.0}))
    payload = fetch_vixts.run(client=object(), now=NOW)
    assert payload["data_date"] == "2026-10-16"
    assert vixts.services() == ["vixts:live"]
    live = vixts.snapshots[0][2]
    assert live["row"] == {"date": "2026-10-19", "vix": 18.0, "vix3m": 20.0, "ratio": 0.9, "spx": 6700.0}
    assert live["current"]["regime"] == fetch_vixts.build_current([live["row"]])["regime"]
    assert live["source"] == "cboe_delayed" and live["quote_time"] == "2026-10-19T14:45:00"
    assert vixts.health == [("vixts", "ok")]


def test_vixts_live_tick_reages_an_old_official_snapshot(vixts, monkeypatch):
    monkeypatch.setattr(fetch_vixts, "_read_json_cache", lambda: _vixts_cached("2026-10-19T05:00:00Z"))
    monkeypatch.setattr(fetch_vixts, "_fetch_live", lambda now: _live({"VIX": 18.0, "VIX3M": 20.0, "SPX": 6700.0}))
    fetch_vixts.run(client=object(), now=NOW)
    assert vixts.services() == ["vixts", "vixts:live"]


@pytest.mark.parametrize("live", [None, _live({"VIX": 99.0, "VIX3M": 20.0, "SPX": 1.0}),
                                  _live({"VIX": 18.0, "VIX3M": 20.0, "SPX": 1.0}, day="2026-10-16")])
def test_vixts_without_a_usable_live_row_keeps_the_official_heartbeat(vixts, monkeypatch, live):
    monkeypatch.setattr(fetch_vixts, "_read_json_cache", lambda: _vixts_cached("2026-10-19T13:00:00Z"))
    monkeypatch.setattr(fetch_vixts, "_fetch_live", lambda now: live)
    fetch_vixts.run(client=object(), now=NOW)
    assert vixts.services() == ["vixts"]


# ── Panic ─────────────────────────────────────────────────────────


def _panic_official(n=300):
    """Synthetic raw closes that warm every 252-session z window."""
    rows = []
    for i in range(n):
        day = datetime.fromordinal(datetime(2025, 8, 1).toordinal() + i).date().isoformat()
        vix = 15 + (i % 7)
        vix3m = 18 + (i % 5)
        rows.append({"date": day, "vix": vix, "vix3m": vix3m, "vvix": 85 + (i % 11),
                     "ts": round(vix / vix3m, 4), "skew": 140 + (i % 13)})
    return rows


def test_panic_live_row_reuses_the_composite_math():
    series = _panic_official()
    official_day = series[-1]["date"]
    day = datetime.fromordinal(datetime.fromisoformat(official_day).toordinal() + 1).date().isoformat()
    values = {"VIX": 30.0, "VIX3M": 22.0, "VVIX": 120.0, "SKEW": 160.0}
    now = datetime.fromisoformat(day + "T19:00:00+00:00")
    live = fetch_panic_index._live_payload(
        {"data_date": official_day, "series": series}, scan_time="t", now=now,
        fetched=_live(values, day=day))
    row = live["row"]
    assert row["date"] == day and row["vix"] == 30.0 and row["ts"] == round(30 / 22, 4)
    assert all(row[f"z_{leg}"] is not None for leg in ("vix", "vvix", "ts", "skew"))
    assert row["level"] == pytest.approx(
        sum(row[f"z_{leg}"] for leg in ("vix", "vvix", "ts", "skew")) / 4, abs=1e-3)
    assert row["delta_1d"] is not None and "_ts_raw" not in row
    assert live["current"]["date"] == day and live["current"]["level"] == row["level"]
    assert live["current"]["legs"]["skew25d"] is None
    assert not math.isnan(row["level"])


def test_panic_live_row_needs_a_newer_session():
    series = _panic_official()
    live = fetch_panic_index._live_payload(
        {"data_date": series[-1]["date"], "series": series}, scan_time="t", now=NOW,
        fetched=_live({"VIX": 1.0, "VIX3M": 1.0, "VVIX": 1.0, "SKEW": 1.0}, day=series[-1]["date"]))
    assert live["row"] is None and live["current"] is None


@pytest.fixture
def panic(monkeypatch, tmp_path):
    rec = _Recorder()
    monkeypatch.setattr(fetch_panic_index, "writer", rec)
    monkeypatch.setattr(fetch_panic_index, "PANIC_INDEX_JSON", tmp_path / "panic.json")
    monkeypatch.setattr(fetch_panic_index, "_fetch_all",
                        lambda *a, **k: (dict.fromkeys(fetch_panic_index._SYMBOLS), {}))
    return rec


def _panic_cached(scan_time):
    series = _panic_official()
    return {"scan_time": scan_time, "source_last_modified": {}, "data_date": series[-1]["date"],
            "count": len(series), "series": series, "current": {"date": series[-1]["date"]},
            "alert": {"last_fired_date": None, "last_fired_kind": None}}


def test_panic_live_tick_skips_the_official_snapshot_and_never_alerts(panic, monkeypatch):
    cached = _panic_cached("2026-10-19T13:00:00Z")
    day = datetime.fromordinal(datetime.fromisoformat(cached["data_date"]).toordinal() + 1).date().isoformat()
    now = datetime.fromisoformat(day + "T19:00:00+00:00")
    cached["scan_time"] = (now.replace(hour=13)).isoformat().replace("+00:00", "Z")
    monkeypatch.setattr(fetch_panic_index, "_read_json_cache", lambda: cached)
    monkeypatch.setattr(fetch_panic_index, "_fetch_live",
                        lambda when: _live({"VIX": 30.0, "VIX3M": 22.0, "VVIX": 120.0, "SKEW": 160.0}, day=day))
    alert = Mock()
    monkeypatch.setattr(fetch_panic_index, "_dispatch_alert", alert)
    fetch_panic_index.run(client=object(), now=now)
    assert panic.services() == ["panic-index:live"]
    assert panic.snapshots[0][2]["row"]["date"] == day
    alert.assert_not_called()
    assert [h[0] for h in panic.health] == ["panic-index"]
