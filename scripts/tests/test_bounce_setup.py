"""BOUNCE SETUP scanner. Red tests against docs/bounce-setup.md.

Pure functions only: every expectation is computed from inline synthetic series,
never from network data.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from bounce_setup_scanner import (
    IV_OFF_PEAK_MIN,
    IV_RUNUP_MIN,
    SKEW_EASE_MIN,
    SLOPE_SESSIONS,
    STAGE2_TOP_N,
    STRETCH_PCTL_MAX,
    WINDOW,
    classify,
    occ_symbol,
    pick_expiry,
    rank_stretch,
    select_fixed_strike,
    skew_easing,
    stage2_tickers,
    vol_plateau,
)


class TestConstants:
    def test_spec_defaults(self):
        assert WINDOW == 20
        assert STAGE2_TOP_N == 30
        assert STRETCH_PCTL_MAX == 10.0
        assert IV_RUNUP_MIN == 2.0
        assert IV_OFF_PEAK_MIN == 0.25
        assert SKEW_EASE_MIN == 0.3
        assert SLOPE_SESSIONS == 3


class TestOccSymbol:
    def test_bac_reference_contract(self):
        assert occ_symbol("bac", "2026-10-16", "P", 55.0) == "BAC261016P00055000"

    def test_fractional_strike(self):
        assert occ_symbol("F", "2026-10-16", "P", 12.5) == "F261016P00012500"


class TestPickExpiry:
    AS_OF = date(2026, 9, 18)

    def test_prefers_a_monthly_in_the_30_to_45_dte_window(self):
        # From 2026-09-10: 2026-10-16 (October third Friday) is 36 DTE. 2026-10-09
        # is a weekly at 29 DTE and 2026-10-23 a weekly at 43 DTE: both skipped.
        expiries = ["2026-10-09", "2026-10-16", "2026-10-23", "2026-11-20"]
        assert pick_expiry(expiries, date(2026, 9, 10)) == "2026-10-16"

    def test_falls_back_to_nearest_monthly_with_at_least_21_dte(self):
        expiries = ["2026-09-25", "2026-10-02", "2026-11-20", "2026-12-18"]
        assert pick_expiry(expiries, self.AS_OF) == "2026-11-20"

    def test_none_when_no_monthly_qualifies(self):
        assert pick_expiry(["2026-09-25", "2026-10-02"], self.AS_OF) is None


class TestSelectFixedStrike:
    def test_nearest_strike_to_window_start_close(self):
        assert select_fixed_strike(54.2, [50.0, 52.5, 55.0, 57.5]) == 55.0

    def test_tie_goes_to_the_lower_strike(self):
        assert select_fixed_strike(53.75, [52.5, 55.0]) == 52.5

    def test_none_without_strikes(self):
        assert select_fixed_strike(54.0, []) is None


def _series(values):
    return [float(v) for v in values]


class TestVolPlateau:
    def test_bac_shape_passes(self):
        # Rises to +4.5, gives back most of it, and is still falling.
        fs = _series([30, 31, 31.2, 31.1, 31, 31.3, 30.8, 32, 32.5, 32.3,
                      33, 33, 34.2, 34.5, 34.3, 33.7, 32.8, 33.1, 32.9, 30.9])
        out = vol_plateau(fs)
        assert out["runup"] == pytest.approx(4.5)
        assert out["off_peak"] == pytest.approx((34.5 - 30.9) / 4.5)
        assert out["slope"] == pytest.approx(30.9 - (32.8 + 33.1 + 32.9) / 3)
        assert out["pass"] is True

    def test_no_runup_fails(self):
        fs = _series([30 + 0.05 * i for i in range(20)])
        assert vol_plateau(fs)["pass"] is False

    def test_still_rising_fails_on_slope(self):
        fs = _series([30] * 10 + [31, 32, 33, 34, 35, 34, 33, 33.5, 33.8, 34.0])
        out = vol_plateau(fs)
        assert out["slope"] > 0
        assert out["pass"] is False

    def test_flat_slope_passes_boundary(self):
        # slope == 0 is a plateau (<= 0); off_peak exactly 0.25 passes (>=).
        fs = _series([30] * 13 + [34, 33, 33, 33, 33, 33, 33])
        out = vol_plateau(fs)
        assert out["runup"] == pytest.approx(4.0)
        assert out["off_peak"] == pytest.approx(0.25)
        assert out["slope"] == pytest.approx(0.0)
        assert out["pass"] is True

    def test_short_series_is_unavailable(self):
        assert vol_plateau(_series([30, 31, 32]))["pass"] is None


class TestSkewEasing:
    def test_bac_shape_passes(self):
        skew = _series([2.62, 2.68, 2.57, 1.75, 2.3, 1.8, 3.0, 2.97, 2.68, 2.63,
                        2.63, 2.6, 2.7, 3.2, 2.58, 3.5, 2.82, 3.47, 3.2, 2.98])
        out = skew_easing(skew)
        assert out["ease"] == pytest.approx(3.5 - 2.98)
        assert out["slope"] == pytest.approx(2.98 - (2.82 + 3.47 + 3.2) / 3)
        assert out["pass"] is True

    def test_flat_slope_fails_strict(self):
        skew = _series([3.0] * 15 + [3.5, 3.0, 3.0, 3.0, 3.0])
        out = skew_easing(skew)
        assert out["slope"] == pytest.approx(0.0)
        assert out["pass"] is False

    def test_small_ease_fails(self):
        skew = _series([3.0] * 16 + [3.2, 3.15, 3.1, 3.0])
        assert skew_easing(skew)["pass"] is False

    def test_short_series_is_unavailable(self):
        assert skew_easing(_series([3.0, 2.9]))["pass"] is None


class TestRankStretch:
    def test_lowest_combined_percentile_ranks_first(self):
        scores = {
            "AAA": {"rsi": 70.0, "pct_b": 1.1, "ret_z": 2.0},
            "BAC": {"rsi": 22.0, "pct_b": -0.2, "ret_z": -2.5},
            "CCC": {"rsi": 45.0, "pct_b": 0.4, "ret_z": -0.1},
            "DDD": {"rsi": 30.0, "pct_b": 0.05, "ret_z": -1.2},
        }
        ranked = rank_stretch(scores)
        assert [row["ticker"] for row in ranked] == ["BAC", "DDD", "CCC", "AAA"]
        assert ranked[0]["stretch_rank"] == 1
        assert ranked[0]["stretch_pctl"] == pytest.approx(0.0)
        assert ranked[-1]["stretch_pctl"] == pytest.approx(100.0)

    def test_names_missing_a_metric_are_excluded(self):
        ranked = rank_stretch({
            "BAC": {"rsi": 22.0, "pct_b": -0.2, "ret_z": -2.5},
            "NEW": {"rsi": 40.0, "pct_b": 0.1, "ret_z": None},
        })
        assert [row["ticker"] for row in ranked] == ["BAC"]

    def test_stage2_takes_only_the_top_n(self):
        scores = {f"T{i:03d}": {"rsi": float(i), "pct_b": i / 100, "ret_z": i / 10} for i in range(80)}
        picked = stage2_tickers(rank_stretch(scores))
        assert len(picked) == STAGE2_TOP_N
        assert picked[0] == "T000"


class TestClassify:
    PASS = {"pass": True}
    FAIL = {"pass": False}
    NA = {"pass": None}

    def test_verdict_table(self):
        assert classify(5.0, self.PASS, self.PASS) == "BOUNCE_SETUP"
        assert classify(5.0, self.PASS, self.FAIL) == "WATCH"
        assert classify(5.0, self.FAIL, self.PASS) == "WATCH"
        assert classify(5.0, self.FAIL, self.FAIL) == "STRETCHED"

    def test_unavailable_leg_is_never_a_pass(self):
        assert classify(5.0, self.NA, self.PASS) == "WATCH"
        assert classify(5.0, self.NA, self.NA) == "STRETCHED"

    def test_stretch_boundary_is_inclusive(self):
        assert classify(10.0, self.PASS, self.PASS) == "BOUNCE_SETUP"
        assert classify(10.1, self.PASS, self.PASS) is None


# ── Implementation tests (appended): flow join, budget block, build_output ──

import bounce_setup_scanner as bss  # noqa: E402


class TestFlowJoin:
    PAYLOAD = {"top_signals": [
        {"ticker": "BAC", "signal": "STRONG", "direction": "ACCUMULATION", "score": 72.5},
        {"ticker": "JPM", "signal": "WEAK", "direction": "distribution", "score": 12},
        {"ticker": "WFC", "signal": "NONE", "direction": "NEUTRAL", "score": 3},
    ]}

    def test_accumulation_maps_direction_and_score(self):
        assert bss.flow_for("bac", self.PAYLOAD) == {"signal": "ACCUMULATION", "score": 72.5}

    def test_other_directions_are_uppercased(self):
        assert bss.flow_for("JPM", self.PAYLOAD) == {"signal": "DISTRIBUTION", "score": 12.0}
        assert bss.flow_for("WFC", self.PAYLOAD)["signal"] == "NEUTRAL"

    def test_absent_ticker_or_snapshot_is_null(self):
        assert bss.flow_for("C", self.PAYLOAD) is None
        assert bss.flow_for("BAC", None) is None


class TestBuildOutput:
    def test_sorts_by_verdict_then_stretch_and_counts(self):
        rows = [
            {"ticker": "A", "verdict": "STRETCHED", "stretch_pctl": 0.1},
            {"ticker": "B", "verdict": "BOUNCE_SETUP", "stretch_pctl": 3.0},
            {"ticker": "C", "verdict": "WATCH", "stretch_pctl": 1.0},
            {"ticker": "D", "verdict": "BOUNCE_SETUP", "stretch_pctl": 0.5},
        ]
        out = bss.build_output(
            rows, as_of="2026-09-18", universe="largecaps",
            coverage={"tickers": 520, "ranked": 512, "excluded_short_history": 8, "stage2": 30},
        )
        assert [r["ticker"] for r in out["results"]] == ["D", "B", "C", "A"]
        assert out["bounce_count"] == 2
        assert out["window"] == WINDOW
        assert out["coverage"] == {"tickers": 520, "ranked": 512, "excluded_short_history": 8, "stage2": 30}


def _closes(n, start=100.0, drift=0.1, tail_drop=0.0):
    import math as _m
    values = [start + drift * i + 2 * _m.sin(i / 3) for i in range(n)]
    for k in range(1, WINDOW + 1):
        values[-k] -= tail_drop * (WINDOW + 1 - k) / WINDOW
    return values


class TestStretchMetrics:
    def test_needs_272_closes(self):
        assert bss.stretch_metrics(_closes(271)) is None
        out = bss.stretch_metrics(_closes(272))
        assert set(out) == {"rsi", "pct_b", "ret_z", "ret_20d"}

    def test_a_selloff_scores_a_negative_ret_z(self):
        assert bss.stretch_metrics(_closes(300, tail_drop=20.0))["ret_z"] < -1


class TestScanUniverse:
    def _setup(self, monkeypatch, tmp_path, budget_blocked):
        universe = [f"T{i:02d}" for i in range(20)]
        closes = {
            t: [(date.fromordinal(date(2025, 7, 1).toordinal() + j).isoformat(), v) for j, v in enumerate(_closes(300, tail_drop=(30.0 if t == "T00" else 0.0) + i * 0.01))]
            for i, t in enumerate(universe)
        }
        monkeypatch.setattr(bss, "resolve_tickers", lambda tickers, preset: (universe, "preset:largecaps"))
        monkeypatch.setattr(bss, "load_closes", lambda tickers, cutoff: closes)
        monkeypatch.setattr(bss, "should_block_universe_scan", lambda: budget_blocked)
        monkeypatch.setattr(bss, "_CACHE_PATH", tmp_path / "bounce_setup.json")
        degraded = []
        monkeypatch.setattr(bss, "record_scan_degraded", lambda *a, **k: degraded.append(a))
        return degraded

    def test_budget_block_skips_stage2_and_records_degraded(self, monkeypatch, tmp_path):
        degraded = self._setup(monkeypatch, tmp_path, budget_blocked=True)

        class NoUW:
            def __getattr__(self, name):
                raise AssertionError(f"UW call {name} under budget block")

        out = bss.scan_universe([], client=NoUW())
        assert out["scan_status"] == bss.SCAN_STATUS_BUDGET_BLOCKED
        assert out["results"] == []
        assert degraded and degraded[0][0] == "bounce-setup"
        assert bss.save_cache(out, tmp_path / "bounce_setup.json") is False

    def test_stage2_only_for_stretched_names_and_joins_flow(self, monkeypatch, tmp_path):
        self._setup(monkeypatch, tmp_path, budget_blocked=False)
        monkeypatch.setattr(bss, "load_flow_payload", lambda: {"top_signals": [
            {"ticker": "T00", "direction": "ACCUMULATION", "score": 80}]})
        seen = []

        class FakeUW:
            def get_expiry_breakdown(self, ticker):
                seen.append(ticker)
                return {"data": []}

            def get_historical_risk_reversal_skew(self, ticker, **kwargs):
                return {"data": []}

        out = bss.scan_universe([], client=FakeUW(), now=datetime(2026, 9, 18, 22, 0, tzinfo=timezone.utc))
        assert out["coverage"]["ranked"] == 20
        assert out["coverage"]["stage2"] == len(set(seen)) >= 1
        row = next(r for r in out["results"] if r["ticker"] == "T00")
        assert row["verdict"] == "STRETCHED"
        assert row["flow"] == {"signal": "ACCUMULATION", "score": 80.0}
        assert len(row["series"]) == WINDOW
        assert row["series"][0]["spot_cum_pct"] == 0.0


from datetime import datetime, timezone  # noqa: E402


class _ChainClient:
    """Fake UW client: one monthly expiry, a strike ladder on both rights, and a
    per-symbol daily IV history. `iv(symbol, i)` returns (iv_decimal, bid, ask)."""

    def __init__(self, dates, iv, strikes=(35.0, 37.5, 40.0, 42.5, 45.0)):
        self.dates, self.iv, self.strikes = dates, iv, strikes
        self.called = []

    def get_expiry_breakdown(self, t):
        return {"data": [{"expiry": "2026-10-16"}]}

    def get_option_contracts(self, t, **kw):
        rows = []
        for k in self.strikes:
            for right in ("P", "C"):
                rows.append({"option_symbol": f"{t}261016{right}{int(round(k * 1000)):08d}",
                             "strike": str(k), "expiry": "2026-10-16"})
        return {"data": rows}

    def get_option_contract_historic(self, symbol):
        self.called.append(symbol)
        rows = []
        for i, d in enumerate(self.dates):
            iv, bid, ask = self.iv(symbol, i)
            rows.append({"date": d, "implied_volatility": str(iv), "nbbo_bid": str(bid), "nbbo_ask": str(ask)})
        return {"chains": rows}

    def get_historical_risk_reversal_skew(self, *a, **k):
        raise AssertionError("the skew leg must not use UW's risk-reversal history")


def _window_dates():
    return [(date(2026, 8, 3) + timedelta(days=i)).isoformat() for i in range(WINDOW + 1)]


def _ranked(ticker="XYZ"):
    return {"ticker": ticker, "stretch_rank": 1, "stretch_pctl": 0.5, "rsi": 20.0,
            "pct_b": -0.2, "ret_z": -2.0, "ret_20d": -6.0}


class TestFixedStrikeSkew:
    def test_moneyness_constants(self):
        import bounce_setup_scanner as mod

        assert mod.SKEW_PUT_MONEYNESS == 0.93
        assert mod.SKEW_CALL_MONEYNESS == 1.07

    def test_wings_are_fixed_at_window_start_and_skew_is_put_minus_call(self):
        import bounce_setup_scanner as mod

        dates = _window_dates()
        n = len(dates)

        def iv(symbol, i):
            if "P00037500" in symbol:  # put wing: rich early, easing late
                return (0.34 + 0.002 * i if i < 15 else 0.37 - 0.01 * (i - 14)), 1.00, 1.05
            if "C00042500" in symbol:  # call wing: flat
                return 0.25, 0.50, 0.52
            return 0.28, 1.50, 1.55  # ATM put

        client = _ChainClient(dates, iv)
        # Window-start close 40.0 -> ATM put 40, put wing nearest 37.2 -> 37.5,
        # call wing nearest 42.8 -> 42.5.
        closes = [(d, 40.0 - 0.05 * i) for i, d in enumerate(dates)]
        row = mod.stage2_row(client, _ranked(), closes, date(2026, 9, 18), None)

        assert row["contract"]["strike"] == 40.0
        assert row["skew_contracts"]["put"]["strike"] == 37.5
        assert row["skew_contracts"]["call"]["strike"] == 42.5
        assert row["skew_contracts"]["put"]["symbol"] == "XYZ261016P00037500"
        assert row["skew_contracts"]["call"]["symbol"] == "XYZ261016C00042500"
        window = dates[-WINDOW:]
        by_date = {p["date"]: p["skew30"] for p in row["series"]}
        for i, d in enumerate(dates):
            if d in window:
                put_iv, _, _ = iv("XYZ261016P00037500", i)
                assert by_date[d] == pytest.approx((put_iv - 0.25) * 100, abs=1e-3)
        assert row["skew"]["pass"] is not None
        assert not any("risk_reversal" in e for e in row["errors"])

    def test_illiquid_wings_disable_only_the_skew_leg(self):
        import bounce_setup_scanner as mod

        dates = _window_dates()

        def iv(symbol, i):
            if "P00040000" in symbol:  # ATM stays tradeable
                return 0.28 + (0.01 * i if i < 12 else 0.12 - 0.01 * (i - 12)), 1.50, 1.55
            return 0.30, 0.20, 1.20  # wings: spread far above 25% of mid

        row = mod.stage2_row(_ChainClient(dates, iv), _ranked(), [(d, 40.0) for d in dates], date(2026, 9, 18), None)
        assert row["vol"]["pass"] is not None
        assert row["skew"]["pass"] is None
        assert any(e.startswith("illiquid_skew_wings") for e in row["errors"])
        assert row["verdict"] in {"WATCH", "STRETCHED"}


class TestLiquidityGate:
    """Illiquid strikes produce model IVs fit to junk quotes (UDR 37.5P,
    2026-09-15: bid 0.75 / ask 4.50, IV 7.4 vs 29.3 the day before). Such days
    must never drive a leg."""

    def test_wide_quote_days_are_dropped(self):
        from bounce_setup_scanner import MAX_REL_SPREAD, _historic_iv

        assert MAX_REL_SPREAD == 0.25
        payload = {"chains": [
            {"date": "2026-09-14", "implied_volatility": "0.29", "nbbo_bid": "2.00", "nbbo_ask": "2.20"},
            {"date": "2026-09-15", "implied_volatility": "0.07", "nbbo_bid": "0.75", "nbbo_ask": "4.50"},
            {"date": "2026-09-16", "implied_volatility": "0.25", "nbbo_bid": "0", "nbbo_ask": "0.40"},
            {"date": "2026-09-17", "implied_volatility": "0.24", "nbbo_bid": "2.40", "nbbo_ask": "2.60"},
        ]}
        assert _historic_iv(payload) == {"2026-09-14": pytest.approx(29.0), "2026-09-17": pytest.approx(24.0)}

    def test_too_few_liquid_sessions_make_both_legs_unavailable(self, monkeypatch):
        import bounce_setup_scanner as mod

        assert mod.MIN_VALID_SESSIONS == 15
        dates = [f"2026-08-{d:02d}" for d in range(3, 29)][:WINDOW + 1]

        class Client:
            def get_expiry_breakdown(self, t):
                return {"data": [{"expiry": "2026-10-16"}]}

            def get_option_contracts(self, t, **kw):
                return {"data": [{"option_symbol": "UDR261016P00037500", "strike": "37.5", "expiry": "2026-10-16"}]}

            def get_option_contract_historic(self, sym):
                # Only every fourth session carries a tradeable quote.
                return {"chains": [
                    {"date": d, "implied_volatility": "0.25",
                     "nbbo_bid": "2.00" if i % 4 == 0 else "0.50",
                     "nbbo_ask": "2.10" if i % 4 == 0 else "4.50"}
                    for i, d in enumerate(dates)
                ]}

        row = mod.stage2_row(
            Client(),
            {"ticker": "UDR", "stretch_rank": 4, "stretch_pctl": 0.6, "rsi": 8.7, "pct_b": -0.3,
             "ret_z": -2.1, "ret_20d": -8.8},
            [(d, 40.0 - 0.1 * i) for i, d in enumerate(dates)],
            date(2026, 9, 18),
            None,
        )
        assert row["verdict"] == "STRETCHED"
        assert row["vol"]["pass"] is None
        assert row["skew"]["pass"] is None
        assert row["liquidity"]["valid_sessions"] < 15
        assert any(e.startswith("illiquid_options") for e in row["errors"])
