"""Tests for repair_cri_rvol_cache.py."""

from datetime import date, timedelta

import numpy as np
import pytest

from repair_cri_rvol_cache import build_repaired_cri_payload


def make_dates(count: int) -> list[str]:
    start = date(2025, 8, 1)
    return [(start + timedelta(days=index)).isoformat() for index in range(count)]


class TestBuildRepairedCriPayload:
    """Regression coverage for the RVOL cache-repair path."""

    def test_rebuilds_twenty_history_rows_with_numeric_rvol(self):
        dates = make_dates(140)
        aligned = {
            "VIX": np.linspace(16.0, 24.0, len(dates)),
            "VVIX": np.linspace(92.0, 118.0, len(dates)),
            "SPY": 540 + np.sin(np.arange(len(dates)) / 3.5) * 14 + np.arange(len(dates)) * 0.35,
        }
        cor1m_by_date = {
            dates[index]: 22.0 + (index - (len(dates) - 12)) * 0.8
            for index in range(len(dates) - 12, len(dates) - 1)
        }
        payload = build_repaired_cri_payload(
            base_cache={"cor1m": 31.25, "cor1m_5d_change": 4.4},
            aligned=aligned,
            common_dates=dates,
            cor1m_by_date=cor1m_by_date,
            scan_time="2026-03-11T10:15:00",
        )

        assert payload["date"] == dates[-1]
        assert payload["scan_time"] == "2026-03-11T10:15:00"
        assert len(payload["history"]) == 20
        assert all(isinstance(entry["realized_vol"], float) for entry in payload["history"])
        assert len(payload["spy_closes"]) == 40
        assert payload["history"][-1]["cor1m"] == pytest.approx(31.25)
        assert payload["history"][0]["cor1m"] is None

    def test_refreshes_top_level_rvol_cta_and_trigger_from_rebuilt_history(self):
        dates = make_dates(140)
        spy = 520 + np.sin(np.arange(len(dates)) / 2.0) * 20 + np.arange(len(dates)) * 0.4
        aligned = {
            "VIX": np.linspace(18.0, 32.0, len(dates)),
            "VVIX": np.linspace(98.0, 134.0, len(dates)),
            "SPY": spy,
        }
        cor1m_by_date = {
            dates[index]: 25.0 + (index - (len(dates) - 8)) * 1.1
            for index in range(len(dates) - 8, len(dates))
        }
        payload = build_repaired_cri_payload(
            base_cache={"cor1m": 33.4},
            aligned=aligned,
            common_dates=dates,
            cor1m_by_date=cor1m_by_date,
            scan_time="2026-03-11T10:30:00",
        )

        expected_change = round(33.4 - cor1m_by_date[dates[-6]], 2)

        assert payload["realized_vol"] == payload["history"][-1]["realized_vol"]
        assert payload["cta"]["realized_vol"] == payload["realized_vol"]
        assert payload["crash_trigger"]["values"]["realized_vol"] == payload["realized_vol"]
        assert payload["cor1m"] == pytest.approx(33.4)
        assert payload["cor1m_5d_change"] == pytest.approx(expected_change)
        assert isinstance(payload["cri"]["score"], float)
