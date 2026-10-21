"""Provider-contract and point-in-time regressions. No live provider access."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import json
from unittest.mock import Mock

import pytest

from clients.uw_advanced_client import UWAdvancedClient, decimal_string, display_futures_price
from clients.uw_client import UWAuthError, UWRateLimitError
from uw_research import collect_research, read_research
from utils.atomic_io import atomic_save

NOW = datetime(2026, 10, 21, 16, 0, tzinfo=timezone.utc)


class FakeClient:
    def __init__(self, overrides=None):
        self.overrides = overrides or {}
        self.calls = []

    def __getattr__(self, name):
        def call(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            value = self.overrides.get(name, {"data": [{"value": "1", "date": "2026-10-20"}]})
            if isinstance(value, Exception):
                raise value
            return value(*args, **kwargs) if callable(value) else value
        return call


def adapter():
    client = object.__new__(UWAdvancedClient)
    client._get = Mock(return_value={"data": []})
    return client


def test_multileg_route_uses_ticker_filter_and_explicit_24h_iso_bounds():
    client = adapter()
    client.get_multi_leg("sndk", newer_than=NOW - timedelta(hours=24), older_than=NOW, offset=50)
    route, query = client._get.call_args.args
    assert route == "option-trades/multi-leg"
    assert query == {"ticker_symbol": "SNDK", "newer_than": "2026-10-20T16:00:00+00:00", "older_than": NOW.isoformat(), "limit": 50, "offset": 50}


@pytest.mark.parametrize("start,end", [(NOW - timedelta(hours=25), NOW), (NOW, NOW), (NOW.replace(tzinfo=None), NOW)])
def test_multileg_rejects_invalid_window_before_io(start, end):
    client = adapter()
    with pytest.raises(ValueError):
        client.get_multi_leg("SPY", newer_than=start, older_than=end)
    client._get.assert_not_called()


@pytest.mark.parametrize("value", ["../SPY", "AAPL?token=x", "SPY/../QQQ", "", "A" * 30])
def test_untrusted_ticker_cannot_escape_endpoint_or_snapshot_path(value, tmp_path):
    with pytest.raises(ValueError):
        collect_research(value, client=FakeClient(), data_dir=tmp_path)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("value,expected", [("-0.000000000000000123", "-0.000000000000000123"), (Decimal("-3.52"), "-3.52"), ("NaN", None), ("Infinity", None), (True, None), (None, None)])
def test_decimal_normalization_preserves_credit_precision_and_unknowns(value, expected):
    assert decimal_string(value) == expected


def test_raw_cme_price_factor_is_explicit_and_rest_price_untouched(tmp_path):
    assert display_futures_price("123456", "0.01") == "1234.56"
    assert display_futures_price("123456", "-0.01") is None
    fake = FakeClient({"get_futures_blocks": {"data": [{"price": "1234.56", "is_block": True}]}, "get_futures_display_factor": {"data": {"security_id": 123, "symbol": "ESZ6", "display_factor": "0.01"}}})
    result = collect_research("SPY", client=fake, now=NOW, data_dir=tmp_path, sections=["futures"], futures_contract="ESZ6", security_id=123)
    assert result["sections"]["futures"]["data"]["blocks"]["data"][0]["price"] == "1234.56"
    assert "already displayed" in result["sections"]["futures"]["data"]["price_basis"]


def test_multileg_decimal_side_is_not_held_position_direction(tmp_path):
    fake = FakeClient({"get_multi_leg": {"data": [{"id": "strategy-1", "net_price": "-3.52000001", "net_delta": "-0.45"}]},
                       "get_multi_leg_legs": {"data": [{"side": "bid", "delta": "-0.25", "size": 5}]}})
    result = collect_research("SNDK", sections=["multi_leg"], client=fake, now=NOW, data_dir=tmp_path)
    row = result["sections"]["multi_leg"]["data"]["trades"][0]
    assert row["net_price"] == "-3.52000001" and row["debit_credit"] == "credit"
    assert row["position_direction_inferred"] is False
    assert row["legs"][0] == {"side": "bid", "delta": "-0.25", "size": 5}


def test_multileg_paging_bound_and_leg_budget_are_visible(tmp_path):
    fake = FakeClient({"get_multi_leg": lambda *a, **kw: {"data": [{"id": f"s-{kw['offset'] + i}", "net_price": "1.0"} for i in range(50)]},
                       "get_multi_leg_legs": {"data": []}})
    result = collect_research("SPY", sections=["multi_leg"], client=fake, now=NOW, data_dir=tmp_path)
    data = result["sections"]["multi_leg"]["data"]
    assert len(data["trades"]) == 100
    assert data["coverage"]["truncated"] is True
    assert result["status"] == "partial"
    assert [c[2]["offset"] for c in fake.calls if c[0] == "get_multi_leg"] == [0, 50]
    assert sum(c[0] == "get_multi_leg_legs" for c in fake.calls) == 3


def test_denied_entitlement_differs_from_legitimate_empty(tmp_path):
    denied = FakeClient({"get_company_profile": UWAuthError("sensitive body must not leak", status_code=403), "get_options_pulse": {"data": []}})
    result = collect_research("SPY", sections=["company", "pulse"], client=denied, now=NOW, data_dir=tmp_path)
    assert result["sections"]["company"]["state"] == "unavailable"
    assert result["sections"]["company"]["error_code"] == "entitlement_denied"
    assert result["sections"]["pulse"]["state"] == "empty"
    assert "sensitive" not in json.dumps(result)
    assert result["status"] == "partial"


def test_malformed_200_is_not_empty_success(tmp_path):
    fake = FakeClient({"get_company_profile": {"message": "not an observation"}})
    result = collect_research("SPY", sections=["company"], client=fake, now=NOW, data_dir=tmp_path)
    assert result["status"] == "unavailable"
    assert result["sections"]["company"]["error_code"] == "configuration_or_schema_invalid"


def test_failed_refresh_retains_old_observation_without_refreshing_timestamp(tmp_path):
    first = collect_research("SPY", sections=["pulse"], client=FakeClient(), now=NOW, data_dir=tmp_path)
    later = NOW + timedelta(minutes=5)
    result = collect_research("SPY", sections=["pulse"], client=FakeClient({"get_options_pulse": UWRateLimitError("quota", status_code=429)}), now=later, data_dir=tmp_path)
    row = result["sections"]["pulse"]
    assert row["state"] == "stale"
    assert row["observed_at"] == first["sections"]["pulse"]["observed_at"]
    assert row["attempted_at"] == later.isoformat()
    assert row["error_code"] == "rate_limited"
    assert row["data"] == first["sections"]["pulse"]["data"]


def test_section_cadence_skips_fresh_requests_and_expires_on_read(tmp_path):
    collect_research("SPY", sections=["pulse", "company"], client=FakeClient(), now=NOW, data_dir=tmp_path)
    fake = FakeClient()
    result = collect_research("SPY", sections=["pulse", "company"], client=fake, now=NOW + timedelta(seconds=30), data_dir=tmp_path)
    assert result["request_count"] == 0 and fake.calls == []
    result = read_research("SPY", data_dir=tmp_path, now=NOW + timedelta(minutes=5))
    assert result["sections"]["pulse"]["state"] == "stale"
    assert result["sections"]["company"]["state"] == "ok"


def test_request_budget_prevents_unbounded_enrichment_and_marks_remaining(tmp_path):
    fake = FakeClient()
    result = collect_research("SPY", sections=["company", "pulse", "volatility"], client=fake, now=NOW, data_dir=tmp_path, max_requests=1)
    assert len(fake.calls) == 1
    assert result["sections"]["pulse"]["error_code"] == "request_budget_exhausted"
    assert result["sections"]["volatility"]["error_code"] == "request_budget_exhausted"


def test_transcript_requires_explicit_fiscal_quarter_no_guessing(tmp_path):
    fake = FakeClient()
    result = collect_research("SPY", sections=["transcript"], client=fake, now=NOW, data_dir=tmp_path)
    assert result["sections"]["transcript"]["state"] == "unavailable"
    assert fake.calls == []


def test_new_quarter_does_not_reuse_previous_transcript(tmp_path):
    collect_research("SPY", sections=["transcript"], quarter="2026Q1", client=FakeClient(), now=NOW, data_dir=tmp_path)
    fake = FakeClient()
    collect_research("SPY", sections=["transcript"], quarter="2026Q2", client=fake, now=NOW, data_dir=tmp_path)
    assert fake.calls[0][1] == ("SPY", "2026Q2")


def test_snapshot_identity_and_integrity_fail_closed(tmp_path):
    atomic_save(str(tmp_path / "SPY.json"), {"schema": 1, "ticker": "QQQ", "sections": {}})
    assert read_research("SPY", data_dir=tmp_path, now=NOW)["missing"] is True
    (tmp_path / "SPY.json").write_text('{"schema":1,"ticker":"SPY","sections":{"pulse":null}}')
    assert read_research("SPY", data_dir=tmp_path, now=NOW)["missing"] is True


def test_wrappers_use_official_paths_and_query_names():
    client = adapter()
    client.get_spot_exposures_by_expiry_strike("SPY")
    assert client._get.call_args.args == ("stock/SPY/spot-exposures/expiry-strike", {"limit": 100, "page": 0})
    client.get_fx_history("usd", "eur")
    assert client._get.call_args.args == ("forex/history", {"from": "USD", "to": "EUR", "interval": "daily"})
    client.get_analytics("SPY", sliding=True)
    assert client._get.call_args.args[0] == "analytics/sliding"
    assert client._get.call_args.args[1]["window_size"] == 20
    client.get_company_transcript("SPY", "2026Q2")
    assert client._get.call_args.args[0] == "companies/SPY/transcripts/2026Q2"
    client.get_futures_display_factor(123, contract="ESZ6")
    assert client._get.call_args.args == ("futures/display_factors/123", {"symbol": "ESZ6"})


def test_refresh_archives_first_observed_payload_without_overwriting_prior(tmp_path):
    collect_research("SPY", sections=["pulse"], client=FakeClient({"get_options_pulse": {"data": [{"score": "1"}]}}), now=NOW, data_dir=tmp_path)
    collect_research("SPY", sections=["pulse"], client=FakeClient({"get_options_pulse": {"data": [{"score": "2"}]}}), now=NOW + timedelta(minutes=5), data_dir=tmp_path)
    history = sorted((tmp_path / "history" / "SPY").glob("*.json"))
    assert len(history) == 2
    assert json.loads(history[0].read_text())["sections"]["pulse"]["data"]["data"][0]["score"] == "1"
    assert json.loads(history[1].read_text())["sections"]["pulse"]["data"]["data"][0]["score"] == "2"


def test_snapshot_lock_rejects_competing_refresh_before_provider_call(tmp_path, monkeypatch):
    import uw_research
    fake = FakeClient()
    with uw_research._snapshot_lock(tmp_path, "SPY"):
        monkeypatch.setattr(uw_research.time, "monotonic", Mock(side_effect=[0.0, 2.0]))
        with pytest.raises(ValueError, match="already in progress"):
            collect_research("SPY", sections=["pulse"], client=fake, now=NOW, data_dir=tmp_path)
    assert fake.calls == []


def test_archive_has_bounded_retention(tmp_path, monkeypatch):
    import uw_research
    monkeypatch.setattr(uw_research, "MAX_ARCHIVES_PER_TICKER", 2)
    for minute in [0, 5, 10]:
        collect_research("SPY", sections=["pulse"], client=FakeClient(), now=NOW + timedelta(minutes=minute), data_dir=tmp_path)
    history = sorted((tmp_path / "history" / "SPY").glob("*.json"))
    assert len(history) == 2
    assert json.loads(history[0].read_text())["generated_at"] == (NOW + timedelta(minutes=5)).isoformat()


def test_scheduled_cli_hydrates_settings_before_validated_universe(monkeypatch, capsys):
    import uw_research
    import uw_stream.config
    events = []
    def hydrate():
        events.append("hydrate")
        monkeypatch.setenv("RADON_UW_RESEARCH_TICKERS", "SPY,QQQ")
    monkeypatch.setattr(uw_stream.config, "hydrate_settings", hydrate)
    def collect(ticker, **kwargs):
        events.append(ticker)
        return {"status": "ok", "ticker": ticker}
    monkeypatch.setattr(uw_research, "collect_research", collect)
    heartbeat = Mock()
    monkeypatch.setattr(uw_research, "_record_health", heartbeat)
    assert uw_research.main(["--scheduled", "--json"]) == 0
    assert events == ["hydrate", "SPY", "QQQ"]
    heartbeat.assert_called_once_with("ok")
    assert len(json.loads(capsys.readouterr().out)["results"]) == 2


def test_scheduled_cli_records_failed_cycle_without_raw_error(monkeypatch, capsys):
    import uw_research
    import uw_stream.config
    monkeypatch.setattr(uw_stream.config, "hydrate_settings", lambda: None)
    monkeypatch.setenv("RADON_UW_RESEARCH_TICKERS", "../unsafe")
    heartbeat = Mock()
    monkeypatch.setattr(uw_research, "_record_health", heartbeat)
    assert uw_research.main(["--scheduled"]) == 1
    heartbeat.assert_called_once_with("unavailable")
    text = capsys.readouterr().out
    assert json.loads(text)["error_code"] == "research_collection_unavailable"
    assert "../unsafe" not in text


def test_futures_rejects_reassigned_security_id_before_reading_blocks(tmp_path):
    fake = FakeClient({"get_futures_display_factor": {"data": {"security_id": 123, "symbol": "ESU6", "display_factor": "0.01"}}})
    result = collect_research("SPY", sections=["futures"], client=fake, now=NOW, data_dir=tmp_path, futures_contract="ESZ6", security_id=123)
    assert result["sections"]["futures"]["state"] == "unavailable"
    assert len(fake.calls) == 1


def test_failed_new_quarter_cannot_retain_unrelated_transcript(tmp_path):
    collect_research("SPY", sections=["transcript"], quarter="2026Q1", client=FakeClient(), now=NOW, data_dir=tmp_path)
    fake = FakeClient({"get_company_transcript": UWAuthError("denied", status_code=403)})
    result = collect_research("SPY", sections=["transcript"], quarter="2026Q2", client=fake, now=NOW, data_dir=tmp_path)
    section = result["sections"]["transcript"]
    assert len(fake.calls) == 1
    assert section["data"] is None and section["state"] == "unavailable"
    assert section["observed_at"] is None
    assert section["request"] == {"schema": 1, "ticker": "SPY", "section": "transcript", "params": {"quarter": "2026Q2"}}


def test_failed_new_futures_contract_cannot_retain_prior_contract(tmp_path):
    first = FakeClient({"get_futures_display_factor": {"data": {"security_id": 123, "symbol": "ESZ6", "display_factor": "0.01"}}})
    collect_research("SPY", sections=["futures"], futures_contract="ESZ6", security_id=123, client=first, now=NOW, data_dir=tmp_path)
    fake = FakeClient({"get_futures_display_factor": UWRateLimitError("limited", status_code=429)})
    result = collect_research("SPY", sections=["futures"], futures_contract="ESH7", security_id=456, client=fake, now=NOW, data_dir=tmp_path)
    section = result["sections"]["futures"]
    assert len(fake.calls) == 1
    assert section["data"] is None and section["state"] == "unavailable"
    assert section["request"]["params"]["contract"] == "ESH7"
    assert section["request"]["params"]["security_id"] == 456


def test_legacy_unknown_request_identity_requires_refetch_and_cannot_retain(tmp_path):
    old = {"schema": 1, "ticker": "SPY", "sections": {"transcript": {"state": "ok", "data": {"data": "old quarter"}, "observed_at": NOW.isoformat(), "expires_at": (NOW + timedelta(days=1)).isoformat()}}}
    atomic_save(str(tmp_path / "SPY.json"), old)
    fake = FakeClient({"get_company_transcript": UWAuthError("denied", status_code=403)})
    result = collect_research("SPY", sections=["transcript"], quarter="2026Q2", client=fake, now=NOW, data_dir=tmp_path)
    assert len(fake.calls) == 1
    assert result["sections"]["transcript"]["data"] is None
    assert result["sections"]["transcript"]["state"] == "unavailable"


def test_same_canonical_quarter_reuses_only_its_own_fresh_observation(tmp_path):
    collect_research("SPY", sections=["transcript"], quarter="2026Q1", client=FakeClient(), now=NOW, data_dir=tmp_path)
    fake = FakeClient()
    result = collect_research("spy", sections=["transcript"], quarter="2026Q1", client=fake, now=NOW, data_dir=tmp_path)
    assert fake.calls == []
    assert result["sections"]["transcript"]["request"]["ticker"] == "SPY"


def test_fixed_analytics_macro_parameters_are_part_of_cache_identity(tmp_path):
    result = collect_research("SPY", sections=["analytics", "macro"], client=FakeClient(), now=NOW, data_dir=tmp_path)
    analytics = result["sections"]["analytics"]["request"]["params"]
    assert analytics["range"] == "2month" and analytics["sliding_window_size"] == 20
    assert analytics["calculations"] == ["MEAN", "STDDEV", "MAX_DRAWDOWN"]
    assert result["sections"]["macro"]["request"]["params"] == {"indicator": "fed-funds"}
