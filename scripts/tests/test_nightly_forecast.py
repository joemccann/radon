"""Function-level tests for the nightly forecast runner (no torch).

``run_nightly`` imports its three step modules lazily via
``from forecasting import backfill_flow_history, calibration_report, flow_surprise``.
Those submodules all exist and import torch-free (their heavy deps are lazy), so
each test stubs the step FUNCTIONS by setattr on the real modules. Patching
``sys.modules`` does not work here: once any sibling test imports the real
submodule, CPython binds it as a package attribute and ``from forecasting import
flow_surprise`` returns that real object, bypassing a ``sys.modules`` swap. The
runner reads the functions as module attributes, so setattr is order-independent.
"""

from __future__ import annotations

import importlib

import forecasting.nightly_forecast as nightly


def _patch(monkeypatch, name, **attrs):
    """Stub ``forecasting.<name>`` step functions on the real module object."""
    module = importlib.import_module(f"forecasting.{name}")
    for key, value in attrs.items():
        monkeypatch.setattr(module, key, value, raising=False)
    return module


def _recorder():
    calls = []

    def record(tag, value):
        calls.append(tag)
        return value

    return calls, record


def test_run_nightly_runs_all_three_and_aggregates(monkeypatch):
    calls, record = _recorder()

    def backfill_from_cache(*, days):
        return record("backfill", {"days": days, "inserted": 7})

    def rank_watchlist_surprise(*, metric, top, lookback_days):
        return record(
            "surprise",
            {"count_ok": 4, "metric": metric, "results": [], "skipped": 1},
        )

    cache_writes = []

    def write_cache(payload):
        cache_writes.append(payload)

    def build_calibration_report(*, metric, lookback_days, persist):
        assert persist is True
        return record(
            "calibration",
            {
                "n_ok": 9,
                "chronos_available": True,
                "chronos_beats_baseline": False,
            },
        )

    _patch(monkeypatch, "backfill_flow_history",
                  backfill_from_cache=backfill_from_cache)
    _patch(monkeypatch, "flow_surprise",
                  rank_watchlist_surprise=rank_watchlist_surprise,
                  write_cache=write_cache)
    _patch(monkeypatch, "calibration_report",
                  build_calibration_report=build_calibration_report)

    out = nightly.run_nightly(
        metric="flow_strength", top=25, lookback_days=250, backfill_days=20
    )

    # All three steps ran, in order.
    assert calls == ["backfill", "surprise", "calibration"]

    # Dashboard cache was written with the surprise payload.
    assert len(cache_writes) == 1
    assert cache_writes[0]["count_ok"] == 4

    # Summary aggregates each step.
    assert "ran_at" in out
    assert out["backfill"] == {"days": 20, "inserted": 7}
    assert out["surprise_count"] == 4
    assert out["calibration"] == {
        "n_ok": 9,
        "chronos_available": True,
        "chronos_beats_baseline": False,
    }


def test_run_nightly_skips_cache_write_when_helper_absent(monkeypatch):
    """``run_nightly`` reads ``write_cache`` via getattr; absence must not raise."""
    _patch(monkeypatch, "backfill_flow_history",
                  backfill_from_cache=lambda *, days: {"inserted": 0})
    fs = _patch(monkeypatch, "flow_surprise",
                  rank_watchlist_surprise=lambda **k: {"count_ok": 2})
    # Real write_cache exists (SECTION D added it); remove it to exercise the
    # getattr(..., None) guard without writing a real cache file during tests.
    monkeypatch.delattr(fs, "write_cache", raising=False)
    _patch(monkeypatch, "calibration_report",
                  build_calibration_report=lambda **k: {
                      "n_ok": 1,
                      "chronos_available": False,
                      "chronos_beats_baseline": None,
                  })

    out = nightly.run_nightly()

    assert out["surprise_count"] == 2
    assert out["calibration"]["chronos_available"] is False


def test_run_nightly_isolates_backfill_failure(monkeypatch):
    """A backfill exception is captured; later steps still run."""
    calls, record = _recorder()

    def boom(*, days):
        raise RuntimeError("cache unreadable")

    _patch(monkeypatch, "backfill_flow_history",
                  backfill_from_cache=boom)
    _patch(monkeypatch, "flow_surprise",
                  rank_watchlist_surprise=lambda **k: record(
                      "surprise", {"count_ok": 3}),
                  write_cache=lambda p: None)
    _patch(monkeypatch, "calibration_report",
                  build_calibration_report=lambda **k: record(
                      "calibration", {
                          "n_ok": 5,
                          "chronos_available": True,
                          "chronos_beats_baseline": True,
                      }))

    out = nightly.run_nightly()

    # Backfill failure recorded as an err, did not abort the run.
    assert "err" in out["backfill"]
    assert "cache unreadable" in out["backfill"]["err"]

    # Downstream steps still executed.
    assert calls == ["surprise", "calibration"]
    assert out["surprise_count"] == 3
    assert out["calibration"]["chronos_beats_baseline"] is True


def test_run_nightly_isolates_calibration_failure(monkeypatch):
    """A calibration exception is captured without sinking earlier results."""
    _patch(monkeypatch, "backfill_flow_history",
                  backfill_from_cache=lambda *, days: {"inserted": 1})
    _patch(monkeypatch, "flow_surprise",
                  rank_watchlist_surprise=lambda **k: {"count_ok": 8},
                  write_cache=lambda p: None)

    def boom(**k):
        raise ValueError("no calibration data")

    _patch(monkeypatch, "calibration_report",
                  build_calibration_report=boom)

    out = nightly.run_nightly()

    assert out["surprise_count"] == 8
    assert "err" in out["calibration"]
    assert "no calibration data" in out["calibration"]["err"]


def test_run_nightly_surprise_failure_yields_none_count(monkeypatch):
    """A surprise exception leaves count None and skips the cache write."""
    cache_writes = []

    def write_cache(payload):
        cache_writes.append(payload)

    def boom(**k):
        raise RuntimeError("surprise blew up")

    _patch(monkeypatch, "backfill_flow_history",
                  backfill_from_cache=lambda *, days: {"inserted": 1})
    _patch(monkeypatch, "flow_surprise",
                  rank_watchlist_surprise=boom,
                  write_cache=write_cache)
    _patch(monkeypatch, "calibration_report",
                  build_calibration_report=lambda **k: {
                      "n_ok": 2,
                      "chronos_available": True,
                      "chronos_beats_baseline": True,
                  })

    out = nightly.run_nightly()

    assert out["surprise_count"] is None
    assert cache_writes == []
    # Calibration still ran.
    assert out["calibration"]["n_ok"] == 2


def test_main_prints_json_only(monkeypatch, capsys):
    """``main`` emits a single JSON object on stdout."""
    monkeypatch.setattr(
        nightly, "run_nightly", lambda **k: {"ran_at": "x", "surprise_count": 1}
    )

    rc = nightly.main(["--metric", "flow_strength", "--top", "5"])

    assert rc == 0
    captured = capsys.readouterr()
    import json

    parsed = json.loads(captured.out)
    assert parsed["surprise_count"] == 1


def test_main_nonzero_when_all_required_stages_fail(monkeypatch, capsys):
    monkeypatch.setattr(
        nightly,
        "run_nightly",
        lambda **k: {
            "ran_at": "x",
            "backfill": {"err": "backfill failed"},
            "surprise_count": None,
            "calibration": {"err": "calibration failed"},
        },
    )

    assert nightly.main([]) == 1


def test_main_nonzero_when_one_required_stage_fails(monkeypatch, capsys):
    monkeypatch.setattr(
        nightly,
        "run_nightly",
        lambda **k: {
            "ran_at": "x",
            "backfill": {"inserted": 1},
            "surprise_count": 4,
            "calibration": {"err": "calibration failed"},
        },
    )

    assert nightly.main([]) == 1
