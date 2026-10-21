"""Offline failure, lifecycle, and source-boundary regressions for High-Low Line."""
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from types import SimpleNamespace

import pytest
import fetch_high_low_line as scan
from test_high_low_line import FIXTURE, NOW, parsed, sample


def raw(data):
    return json.dumps(data).encode()


def fake_writer(monkeypatch, *, fail=None):
    calls = []
    def record(name):
        def run(*args, **kwargs):
            calls.append((name, args, kwargs))
            if name == fail:
                raise RuntimeError("write failed")
        return run
    monkeypatch.setattr(scan, "writer", SimpleNamespace(**{name: record(name) for name in (
        "ensure_no_replica_for_writers", "upsert_high_low_line_rows", "upsert_scan_snapshot", "record_service_health")}))
    return calls


@pytest.mark.parametrize("mutation", [
    lambda d: d["quote"].update(name="NYSE all issues"),
    lambda d: d["quote"].update(symbol="$NYHGH"),
    lambda d: d["history"].update(period="W"),
    lambda d: d["history"].update(intervals=[]),
    lambda d: d["history"]["intervals"].append(deepcopy(d["history"]["intervals"][0])),
    lambda d: d["history"]["intervals"][0]["end"].update(zone="UTC"),
    lambda d: d["history"]["intervals"][0]["end"].update(millis=0),
    lambda d: d["history"]["intervals"][0].update(source="UNVERIFIED"),
    lambda d: d["history"]["intervals"][0].update(high=999),
])
def test_reject_changed_source_schema(mutation):
    data = deepcopy(FIXTURE["highs"])
    mutation(data)
    with pytest.raises(ValueError):
        scan.parse_stockcharts_history(raw(data), "!NEWHINYC", now=NOW)


def test_unknown_null_not_treated_as_market_closure():
    data = deepcopy(FIXTURE["highs"])
    item = data["history"]["intervals"][0]
    item.update(source=None, open=None, high=None, low=None, close=None)
    with pytest.raises(ValueError):
        scan.parse_stockcharts_history(raw(data), "!NEWHINYC", now=NOW)


def test_future_session_rejected():
    with pytest.raises(ValueError):
        scan.parse_stockcharts_history(raw(FIXTURE["highs"]), "!NEWHINYC", now=datetime(2026, 10, 19, 23, tzinfo=timezone.utc))


def test_intraday_and_pre_18_et_today_are_excluded():
    data = deepcopy(FIXTURE["highs"])
    cutoff = datetime(2026, 10, 20, 21, 59, tzinfo=timezone.utc)
    result = scan.parse_stockcharts_history(raw(data), "!NEWHINYC", now=cutoff)
    assert result["rows"][-1]["date"] == "2026-10-19"
    data["history"]["intervals"][-1]["source"] = "OTHER"
    assert scan.parse_stockcharts_history(raw(data), "!NEWHINYC", now=NOW)["rows"][-1]["date"] == "2026-10-19"
    result = scan.parse_stockcharts_history(raw(FIXTURE["highs"]), "!NEWHINYC", now=datetime(2026, 10, 20, 22, tzinfo=timezone.utc))
    assert result["rows"][-1]["date"] == "2026-10-20"


def test_paired_closure_mismatch_is_not_discarded():
    high, low = parsed([1, 2]), parsed([0, 0])
    high["non_observations"] = ["2012-10-29"]
    with pytest.raises(ValueError):
        scan.build_output(high, low, now=NOW)


@pytest.mark.parametrize("problem", ["duplicate", "start", "weekend", "null", "closure"])
def test_build_refuses_invalid_normalized_inputs(problem):
    high, low = parsed([1, 2]), parsed([0, 0])
    if problem == "duplicate": high["rows"].append(high["rows"][0])
    if problem == "start": high["rows"][0]["date"] = "2003-01-01"
    if problem == "weekend": high["rows"][0]["date"] = "2003-01-04"
    if problem == "null": high["rows"][0]["value"] = None
    if problem == "closure": high["non_observations"] = ["2003-01-01"]
    with pytest.raises(ValueError): scan.build_output(high, low, now=NOW)


def test_forming_week_and_conservative_friday_18_et_boundary():
    high, low = parsed([1]), parsed([0])
    before = scan.build_output(high, low, now=datetime(2003, 1, 3, 22, 59, tzinfo=timezone.utc))
    after = scan.build_output(high, low, now=datetime(2003, 1, 3, 23, tzinfo=timezone.utc))
    assert before["weekly_current"]["period_complete"] is False
    assert after["weekly_current"]["period_complete"] is True
    assert before["weekly_current"]["period_start"] == "2002-12-30"
    assert after["weekly_current"]["period_end"] == "2003-01-03"


def test_weekly_oscillators_recompute_from_weekly_closes():
    data = scan.build_output(parsed([20, 15, 22, 9, 30] * 40), parsed([5, 18, 4, 14, 6] * 40), now=NOW)
    rows = data["weekly_series"]
    assert [r["stoch_rsi14"] for r in rows] == scan.stoch_rsi([r["cumulative"] for r in rows])
    assert rows[-1]["cumulative"] == data["current"]["cumulative"]


def test_reset_requires_prior_positive_exact_zero(monkeypatch):
    monkeypatch.setattr(scan, "stoch_rsi", lambda values: [None, .4, .00001, 0, 0, None, 0])
    rows = [{"cumulative": i} for i in range(7)]
    scan._oscillators(rows, [{"high": i, "low": i, "close": i} for i in range(7)])
    assert [r["reset_to_zero"] for r in rows] == [False, False, False, True, False, False, False]
    assert scan._zone({"stoch_rsi14": .2}) == "oversold"
    assert scan._zone({"stoch_rsi14": .8}) == "overbought"
    assert scan._zone({"stoch_rsi14": .5}) == "neutral"


@pytest.mark.parametrize("period", [0, -1, True, 1.5])
def test_bad_period_fails(period):
    with pytest.raises(ValueError): scan.wilder_rsi([1, 2], period)


@pytest.mark.parametrize("values", [[True], [float("nan")], [float("inf")]])
def test_nonfinite_math_fails(values):
    with pytest.raises(ValueError): scan.wilder_rsi(values)


def test_rsi_edges_and_sar_seed_acceleration():
    assert scan.wilder_rsi([1] * 20)[-1] == 50
    assert scan.wilder_rsi(list(range(20)))[-1] == 100
    assert scan.wilder_rsi(list(range(20, 0, -1)))[-1] == 0
    assert scan.parabolic_sar([]) == []
    assert scan.parabolic_sar([{"high": 1, "low": 1, "close": 1}]) == [(None, None)]
    rows = [{"high": v + 2, "low": v, "close": v + 1} for v in range(10, 20)]
    result = scan.parabolic_sar(rows, start=.02, increment=.02, maximum=.04)
    assert result[3][0] == pytest.approx(10.16)
    with pytest.raises(ValueError): scan.parabolic_sar(rows, maximum=.1)
    with pytest.raises(ValueError): scan.parabolic_sar([{"high": 0, "low": 1, "close": 2}])


def test_source_hash_stable_across_poll_and_revisions_change_it():
    data = sample()
    changed = scan.build_output(parsed([21, 15, 22, 9, 30] * 12), parsed([5, 18, 4, 14, 6] * 12), now=NOW)
    assert changed["source_hash"] != data["source_hash"]
    assert changed["series"][-1]["cumulative"] - data["series"][-1]["cumulative"] == 12
    assert data["degraded"] is True


def test_persist_writes_both_intervals_then_snapshot_health_and_atomic_file(monkeypatch, tmp_path):
    calls = fake_writer(monkeypatch)
    monkeypatch.setattr(scan, "CACHE_PATH", tmp_path / "cache.json")
    data = sample()
    scan.persist_result(data)
    assert [c[0] for c in calls] == ["ensure_no_replica_for_writers", "upsert_high_low_line_rows", "upsert_high_low_line_rows", "upsert_scan_snapshot", "record_service_health"]
    assert [c[2]["interval"] for c in calls[1:3]] == ["daily", "weekly"]
    assert calls[3][2] == {"bounded": True}
    assert calls[-1][1] == ("high-low-line", "ok")
    assert scan.verified_load(str(scan.CACHE_PATH))["source_hash"] == data["source_hash"]


@pytest.mark.parametrize("fail", ["upsert_high_low_line_rows", "upsert_scan_snapshot", "record_service_health"])
def test_failed_persist_retains_disk_and_restores_environment(monkeypatch, tmp_path, fail):
    fake_writer(monkeypatch, fail=fail)
    monkeypatch.setattr(scan, "CACHE_PATH", tmp_path / "cache.json")
    scan.CACHE_PATH.write_text("last good")
    monkeypatch.delenv("RADON_DB_NO_REPLICA", raising=False)
    with pytest.raises(RuntimeError): scan.persist_result(sample())
    assert scan.CACHE_PATH.read_text() == "last good"
    assert "RADON_DB_NO_REPLICA" not in os.environ


@pytest.mark.parametrize("key,value", [("count", 0), ("source_hash", "bad"), ("missing", True), ("series", []), ("weekly_series", [])])
def test_payload_validation_before_any_writes(monkeypatch, key, value):
    calls = fake_writer(monkeypatch)
    data = sample()
    data[key] = value
    with pytest.raises(ValueError): scan.persist_result(data)
    assert calls == []


def test_collect_fetches_exact_symbols_sequentially():
    calls = []
    def fetch(symbol, **kwargs):
        calls.append(symbol)
        return raw(FIXTURE["highs" if symbol == "!NEWHINYC" else "lows"])
    data = scan.collect(SimpleNamespace(fetch_history=fetch), NOW)
    assert calls == ["!NEWHINYC", "!NEWLONYC"]
    assert data["current"]["net"] == 54


def test_owned_client_closed(monkeypatch):
    class Client:
        def __enter__(self): return self
        def __exit__(self, *args): self.closed = True
        def fetch_history(self, symbol, **kwargs): return raw(FIXTURE["highs" if symbol == "!NEWHINYC" else "lows"])
    client = Client()
    monkeypatch.setattr(scan, "HighLowSourceClient", lambda: client)
    assert scan.collect(now=NOW)["current"]["net"] == 54
    assert client.closed


def test_cli_json_and_failures_keep_stdout_clean(monkeypatch, capsys):
    data = sample()
    calls = fake_writer(monkeypatch)
    monkeypatch.setattr(scan, "_read_previous", lambda: None)
    monkeypatch.setattr(scan, "collect", lambda: data)
    monkeypatch.setattr(scan, "persist_result", lambda *args, **kwargs: None)
    assert scan.main(["--json"]) == 0
    out = capsys.readouterr()
    assert json.loads(out.out)["source_hash"] == data["source_hash"]
    assert "Data Freshness:" in out.err
    def fail(): raise ValueError("invalid source")
    monkeypatch.setattr(scan, "collect", fail)
    assert scan.main([]) == 1
    out = capsys.readouterr()
    assert out.out == "" and "invalid source" in out.err
    assert calls[-1][1] == ("high-low-line", "error")


def test_failure_heartbeat_failure_still_nonzero(monkeypatch, capsys):
    fake_writer(monkeypatch, fail="record_service_health")
    scan._record_failure(ValueError("bad"))
    assert "error heartbeat failed" in capsys.readouterr().err


def test_previous_turso_first_then_disk(monkeypatch, tmp_path):
    import db.hrana_http as http
    data = sample()
    monkeypatch.setattr(scan, "CACHE_PATH", tmp_path / "cache.json")
    text = json.dumps(data)
    def query(sql, args):
        if "length(payload)" in sql: return [(data["scan_time"], len(text))]
        return [(text[args[0] - 1:args[0] - 1 + args[1]],)]
    monkeypatch.setattr(http, "hrana_query", query)
    assert scan._read_previous() == data
    monkeypatch.setattr(http, "hrana_query", lambda *args: [])
    scan.atomic_save(str(scan.CACHE_PATH), data)
    assert scan._read_previous() == data
    scan.CACHE_PATH.write_text("invalid")
    assert scan._read_previous() is None
    def fail(*args): raise RuntimeError("offline")
    monkeypatch.setattr(http, "hrana_query", fail)
    with pytest.raises(RuntimeError, match="retention"):
        scan._read_previous()


def test_naive_clock_and_empty_source_rejected(monkeypatch):
    with pytest.raises(ValueError): scan.build_output(parsed([1]), parsed([0]), now=datetime(2026, 10, 20))
    with pytest.raises(ValueError): scan.parse_stockcharts_history(raw(FIXTURE["highs"]), "WRONG", now=NOW)
    data = deepcopy(FIXTURE["highs"])
    data["history"]["intervals"] = [data["history"]["intervals"][-1]]
    with pytest.raises(ValueError): scan.parse_stockcharts_history(raw(data), "!NEWHINYC", now=datetime(2026, 10, 20, 21, tzinfo=timezone.utc))
    monkeypatch.setattr(scan, "MAX_RESPONSE_BYTES", 1)
    with pytest.raises(ValueError): scan.parse_stockcharts_history(raw(data), "!NEWHINYC", now=NOW)


def test_full_synthetic_history_independent_decimal_reference():
    import gzip
    from pathlib import Path
    data = json.loads(gzip.decompress((Path(__file__).parent / "fixtures/high_low_line_synthetic_history.json.gz").read_bytes()))
    result = scan.build_output(data["highs"], data["lows"], now=NOW)
    assert data["_fixture"]["synthetic"] is True
    assert result["count"] == 6207 and result["weekly_count"] == 1243
    assert result["source"]["fractional_observations"] == 143
    assert result["current"]["cumulative"] == 139634.9995
    assert result["current"]["net"] == 54
    # Independently derived from synthetic counts with Decimal precision 60;
    # reference calculation imports no collector or production formula.
    assert result["current"]["rsi14"] == pytest.approx(66.083181406573957, abs=1e-10)
    assert result["current"]["stoch_rsi14"] == pytest.approx(.92786228516838308, abs=1e-10)
    assert result["weekly_current"]["rsi14"] == pytest.approx(90.639927462584081, abs=1e-10)
    assert result["weekly_current"]["stoch_rsi14"] == pytest.approx(.20605160499283732, abs=1e-10)
    assert result["weekly_current"]["highs"] == 273 and result["weekly_current"]["lows"] == 149
    assert result["current"]["sar"] == 139510.9995
    assert result["current"]["sar_direction"] == "rising"
    assert result["weekly_current"]["sar"] == 139321.9995
    assert result["weekly_current"]["sar_direction"] == "rising"
    assert result["weekly_current"]["period_complete"] is False
    assert len(json.dumps(result).encode()) < 3 * 1024 * 1024


def test_previous_large_snapshot_uses_fixed_size_pinned_chunks(monkeypatch):
    import db.hrana_http as http
    import gzip
    from pathlib import Path
    fixture = json.loads(gzip.decompress((Path(__file__).parent / "fixtures/high_low_line_synthetic_history.json.gz").read_bytes()))
    data = scan.build_output(fixture["highs"], fixture["lows"], now=NOW)
    text, calls = json.dumps(data), []
    assert len(text) > 2 * 1024 * 1024
    def query(sql, args):
        calls.append((sql, args))
        if "length(payload)" in sql: return [(data["scan_time"], len(text))]
        assert args[1] == 128 * 1024
        assert args[2:] == ("high-low-line", data["scan_time"])
        return [(text[args[0] - 1:args[0] - 1 + args[1]],)]
    monkeypatch.setattr(http, "hrana_query", query)
    assert scan._read_previous() == data
    assert len(calls) > 16  # A multi-megabyte fixture never becomes one giant Hrana result.


@pytest.mark.parametrize("size,part", [(0, ""), (5 * 1024 * 1024, ""), (99, "truncated")])
def test_previous_invalid_size_or_missing_chunk_refuses_without_cache(monkeypatch, tmp_path, size, part):
    import db.hrana_http as http
    monkeypatch.setattr(scan, "CACHE_PATH", tmp_path / "missing.json")
    monkeypatch.setattr(http, "hrana_query", lambda sql, args: [(NOW.isoformat(), size)] if "length(payload)" in sql else [(part,)])
    with pytest.raises(RuntimeError, match="retention"):
        scan._read_previous()
