"""Gate tests for vol_skew_mr_scanner.py (Lakha / Options Insight framing)."""
from __future__ import annotations

import json
from contextlib import contextmanager

import pytest

import vol_skew_mr_scanner as vsmr


@pytest.fixture(autouse=True)
def _no_live_scan_ib(monkeypatch) -> None:
    @contextmanager
    def _none():
        yield None

    monkeypatch.setattr(vsmr, "scan_ib_session", _none)


def _rising_closes(start: float = 100.0, n: int = 30, step: float = 1.5) -> list[float]:
    return [start + i * step for i in range(n)]


def _falling_closes(start: float = 145.0, n: int = 30, step: float = 1.5) -> list[float]:
    return [start - i * step for i in range(n)]


def _flat_closes(level: float = 100.0, n: int = 30) -> list[float]:
    return [level] * n


def test_rsi_marks_extended_high_after_a_straight_rally() -> None:
    rsi = vsmr.rsi(_rising_closes())
    assert rsi is not None
    assert rsi >= 70


def test_rsi_marks_extended_low_after_a_straight_selloff() -> None:
    rsi = vsmr.rsi(_falling_closes())
    assert rsi is not None
    assert rsi <= 30


def test_bollinger_pct_b_above_upper_band_on_late_spike() -> None:
    prices = _flat_closes(100.0, 19) + [112.0]
    pct_b = vsmr.bollinger_pct_b(prices)
    assert pct_b is not None
    assert pct_b >= 1.0


def test_bollinger_pct_b_below_lower_band_on_late_dump() -> None:
    prices = _flat_closes(100.0, 19) + [88.0]
    pct_b = vsmr.bollinger_pct_b(prices)
    assert pct_b is not None
    assert pct_b <= 0.0


def test_spot_extension_high_from_rsi_or_pct_b() -> None:
    assert vsmr.spot_extension(rsi=78.0, pct_b=0.6) == "HIGH"
    assert vsmr.spot_extension(rsi=55.0, pct_b=1.05) == "HIGH"


def test_spot_extension_low_from_rsi_or_pct_b() -> None:
    assert vsmr.spot_extension(rsi=22.0, pct_b=0.4) == "LOW"
    assert vsmr.spot_extension(rsi=48.0, pct_b=-0.1) == "LOW"


def test_spot_extension_mid_when_neither_gate_fires() -> None:
    assert vsmr.spot_extension(rsi=52.0, pct_b=0.5) == "MID"
    assert vsmr.spot_extension(rsi=None, pct_b=None) == "MID"


def test_series_path_falling_flat_rising() -> None:
    assert vsmr.series_path([32.0, 31.8, 30.4]) == "falling"
    assert vsmr.series_path([20.0, 20.1, 20.05]) == "flat"
    assert vsmr.series_path([18.0, 19.2, 22.0]) == "rising"
    assert vsmr.series_path([21.0]) == "unknown"


def test_verdict_top_mr_extended_high_falling_or_flat_iv() -> None:
    falling = vsmr.classify_gates(extension="HIGH", iv_path="falling", skew_path="falling")
    flat = vsmr.classify_gates(extension="HIGH", iv_path="flat", skew_path="unknown")
    assert falling.verdict == "TOP_MR"
    assert flat.verdict == "TOP_MR"


def test_verdict_breakout_extended_high_rising_iv() -> None:
    result = vsmr.classify_gates(extension="HIGH", iv_path="rising", skew_path="rising")
    assert result.verdict == "BREAKOUT"


def test_verdict_bottom_mr_extended_low_falling_or_flat_iv() -> None:
    falling = vsmr.classify_gates(extension="LOW", iv_path="falling", skew_path="falling")
    flat = vsmr.classify_gates(extension="LOW", iv_path="flat", skew_path="unknown")
    assert falling.verdict == "BOTTOM_MR"
    assert flat.verdict == "BOTTOM_MR"


def test_verdict_breakdown_extended_low_rising_iv() -> None:
    result = vsmr.classify_gates(extension="LOW", iv_path="rising", skew_path="rising")
    assert result.verdict == "BREAKDOWN"


def test_verdict_no_signal_when_spot_is_not_extended() -> None:
    result = vsmr.classify_gates(extension="MID", iv_path="falling", skew_path="falling")
    assert result.verdict == "NO_SIGNAL"
    assert result.suggested_structure is None


def test_put_spread_when_top_mr_and_skew_diverges() -> None:
    result = vsmr.classify_gates(extension="HIGH", iv_path="falling", skew_path="falling")
    assert result.verdict == "TOP_MR"
    assert result.suggested_structure == "put spread"
    assert result.gates["skew"] is True


def test_call_spread_when_bottom_mr_and_skew_diverges() -> None:
    result = vsmr.classify_gates(extension="LOW", iv_path="flat", skew_path="falling")
    assert result.verdict == "BOTTOM_MR"
    assert result.suggested_structure == "call spread"
    assert result.gates["skew"] is True


def test_no_structure_when_skew_confirms_the_spot_move() -> None:
    top = vsmr.classify_gates(extension="HIGH", iv_path="falling", skew_path="rising")
    bottom = vsmr.classify_gates(extension="LOW", iv_path="falling", skew_path="rising")
    assert top.verdict == "TOP_MR"
    assert top.suggested_structure is None
    assert top.gates["skew"] is False
    assert bottom.verdict == "BOTTOM_MR"
    assert bottom.suggested_structure is None
    assert bottom.gates["skew"] is False


def test_aapl_class_top_bottom_and_continue_labels() -> None:
    top = vsmr.classify_from_series(
        prices=_rising_closes(),
        iv_series=[28.0, 26.5, 24.0],
        skew_series=[4.2, 3.1, 1.8],
    )
    bottom = vsmr.classify_from_series(
        prices=_falling_closes(),
        iv_series=[35.0, 33.0, 31.0],
        skew_series=[6.0, 4.4, 2.9],
    )
    follow = vsmr.classify_from_series(
        prices=_rising_closes(),
        iv_series=[18.0, 21.0, 26.0],
        skew_series=[1.0, 1.8, 3.2],
    )
    assert top.verdict == "TOP_MR"
    assert top.suggested_structure == "put spread"
    assert bottom.verdict == "BOTTOM_MR"
    assert bottom.suggested_structure == "call spread"
    assert follow.verdict == "BREAKOUT"


def test_build_output_counts_actionable_mr_only() -> None:
    top = vsmr.VolSkewCandidate(
        ticker="AAPL",
        verdict="TOP_MR",
        spot=212.4,
        rsi=78.2,
        pct_b=1.04,
        extension="HIGH",
        iv_path="falling",
        skew_path="falling",
        suggested_structure="put spread",
        gates={"technicals": True, "iv": True, "skew": True},
        errors=[],
    )
    none = vsmr.VolSkewCandidate(
        ticker="MSFT",
        verdict="NO_SIGNAL",
        spot=400.0,
        rsi=52.0,
        pct_b=0.5,
        extension="MID",
        iv_path="flat",
        skew_path="flat",
        suggested_structure=None,
        gates={"technicals": False, "iv": False, "skew": False},
        errors=[],
    )
    payload = vsmr.build_output([none, top], "explicit", 2, requested_tickers=["AAPL", "MSFT"])
    assert payload["actionable_count"] == 1
    assert payload["candidates_found"] == 2
    assert payload["results"][0]["ticker"] == "AAPL"
    assert payload["results"][0]["verdict"] == "TOP_MR"


def test_resolve_explicit_tickers_overrides_preset() -> None:
    tickers, source = vsmr.resolve_tickers(["aapl", "AAPL", "2026"], "ndx100")
    assert tickers == ["AAPL"]
    assert source == "explicit"


def test_save_cache_preserves_last_good_on_empty_scan(tmp_path, monkeypatch) -> None:
    mirrored: list[object] = []
    monkeypatch.setattr(vsmr, "mirror_scan_snapshot", lambda *a, **_k: mirrored.append(a))
    path = tmp_path / "vol_skew_mr.json"
    good = vsmr.build_output(
        [
            vsmr.VolSkewCandidate(
                ticker="AAPL",
                verdict="TOP_MR",
                spot=212.4,
                rsi=78.2,
                pct_b=1.04,
                extension="HIGH",
                iv_path="falling",
                skew_path="falling",
                suggested_structure="put spread",
                gates={"technicals": True, "iv": True, "skew": True},
                errors=[],
            )
        ],
        "preset:ndx100",
        102,
        requested_tickers=["AAPL"],
    )
    assert vsmr.save_cache(good, path) is True
    empty = vsmr.build_output([], "preset:ndx100", 102, requested_tickers=["AAPL"])
    empty["coverage"] = {
        "tickers": 102, "ok": 0, "no_setup": 0, "rate_limited": 102, "errors": 0, "completed": 0,
    }
    assert vsmr.save_cache(empty, path) is False
    stored = json.loads(path.read_text())
    assert stored["actionable_count"] == 1
    assert stored["results"][0]["ticker"] == "AAPL"
    assert mirrored == [("vol-skew-mr", good)]


class _FakeClient:
    """Records every UW method the scanner reaches for."""

    def __init__(self, *, iv_rank=None, rr=None, contracts=None, fail: str = "") -> None:
        self.calls: list[str] = []
        self._iv_rank = iv_rank if iv_rank is not None else {"data": []}
        self._rr = rr if rr is not None else {"data": []}
        self._contracts = contracts if contracts is not None else {"data": []}
        self._fail = fail

    def _guard(self, name: str):
        self.calls.append(name)
        if self._fail == name:
            raise RuntimeError(f"{name} unavailable")

    def get_stock_ohlc(self, ticker, **_kwargs):
        self._guard("get_stock_ohlc")
        return {"data": [{"close": price} for price in _rising_closes()]}

    def get_iv_rank(self, ticker, **_kwargs):
        self._guard("get_iv_rank")
        return self._iv_rank

    def get_historical_risk_reversal_skew(self, ticker, **_kwargs):
        self._guard("get_historical_risk_reversal_skew")
        return self._rr

    def get_option_contracts(self, ticker, **_kwargs):
        self._guard("get_option_contracts")
        return self._contracts

    def get_greek_exposure_by_strike(self, ticker, **_kwargs):
        self._guard("get_greek_exposure_by_strike")
        return {"data": []}


def _iv_rows(values: list[float]) -> dict:
    return {"data": [{"date": f"2026-09-{idx + 1:02d}", "volatility": value} for idx, value in enumerate(values)]}


def _rr_rows(values: list[float]) -> dict:
    return {"data": [{"date": f"2026-09-{idx + 1:02d}", "value": value} for idx, value in enumerate(values)]}


def test_decimal_risk_reversal_skew_is_read_in_vol_points() -> None:
    decimal_series = vsmr._as_vol_points(vsmr._dated_series(_rr_rows([0.012, 0.020, 0.031]), ("value",)))
    assert decimal_series == pytest.approx([1.2, 2.0, 3.1])
    assert vsmr.series_path(decimal_series) == "rising"


def test_vol_point_risk_reversal_skew_is_left_alone() -> None:
    assert vsmr._as_vol_points(vsmr._dated_series(_rr_rows([6.0, 4.4, 2.9]), ("value",))) == [6.0, 4.4, 2.9]


def test_negative_decimal_skew_keeps_its_sign() -> None:
    assert vsmr._as_vol_points([-0.042, -0.031]) == pytest.approx([-4.2, -3.1])


def test_scan_ticker_survives_an_iv_rank_failure() -> None:
    client = _FakeClient(fail="get_iv_rank", rr=_rr_rows([6.0, 4.4, 2.9]))
    row = vsmr.scan_ticker("AAPL", client)
    assert row is not None
    assert row.ticker == "AAPL"
    assert row.iv_path == "unknown"
    assert any(error.startswith("iv_rank:") for error in row.errors)


def test_scan_ticker_never_spends_quota_on_greek_exposure() -> None:
    client = _FakeClient(iv_rank=_iv_rows([28.0, 26.5, 24.0]), rr=_rr_rows([6.0, 4.4, 2.9]))
    row = vsmr.scan_ticker("AAPL", client)
    assert row is not None
    assert row.verdict == "TOP_MR"
    assert row.suggested_structure == "put spread"
    assert "get_greek_exposure_by_strike" not in client.calls


def test_scan_ticker_skips_the_contract_chain_when_skew_history_exists() -> None:
    client = _FakeClient(iv_rank=_iv_rows([28.0, 26.5, 24.0]), rr=_rr_rows([6.0, 4.4, 2.9]))
    vsmr.scan_ticker("AAPL", client)
    assert "get_option_contracts" not in client.calls


def test_scan_ticker_falls_back_to_the_chain_without_skew_history() -> None:
    client = _FakeClient(iv_rank=_iv_rows([28.0, 26.5, 24.0]))
    vsmr.scan_ticker("AAPL", client)
    assert "get_option_contracts" in client.calls


def test_put_call_skew_ignores_contracts_without_a_delta() -> None:
    contracts = {
        "data": [
            {"option_symbol": "AAPL260116C00300000", "implied_volatility": 0.10},
            {"option_symbol": "AAPL260116C00250000", "implied_volatility": 0.20, "delta": 0.25},
            {"option_symbol": "AAPL260116P00150000", "implied_volatility": 0.32, "delta": -0.25},
        ]
    }
    assert vsmr._current_put_call_skew(contracts) == pytest.approx(12.0)


def test_put_call_skew_is_none_when_no_side_reports_a_delta() -> None:
    contracts = {
        "data": [
            {"option_symbol": "AAPL260116C00250000", "implied_volatility": 0.20},
            {"option_symbol": "AAPL260116P00150000", "implied_volatility": 0.32},
        ]
    }
    assert vsmr._current_put_call_skew(contracts) is None
