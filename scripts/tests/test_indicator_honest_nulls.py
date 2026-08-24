"""REL-067 / R-160, R-161, R-162 (P1) + R-191, R-192 (P2) — every indicator's
degenerate case renders as its own reassuring label.

R-160: `classify_state(None)` returns `"neutral"` — the same string a genuine
mid-range TRIN produces. `moving_average` returns None below 10 hourly bars,
so a first install, a post-retention truncation or a cache rebuild all land
there, and `current.state` is the field the tab colours on.

R-161: `classify_regime` maps every missing or non-finite return onto
`"coupled"`, which is ALSO the benign risk-on label. The indicator exists to
surface `"divergent"`, so its failure mode reads as "credit and equities in
agreement".

R-162: `pct_rank` returns `0.0` when the window has a single distinct ratio,
i.e. the STRONGEST risk-on reading this indicator emits ("IEI/HYG at the
bottom of its 52-week range") fabricated from no data — while
`classify_state` correctly says `"neutral"` for the same input. The two
published fields contradict each other.

R-191: the same shape in ivrank — `pct_window` returns 0.0 where
`rank_window` returns None, and `has_rank`'s `or` makes that 0.0 sufficient
to unlock the 1-year low/high block.

R-192: a failed Turso row upsert still writes `data/*.json` and exits 0.
`_write_db` wraps the row upsert, the snapshot AND the heartbeat in one
`except`, so a Hrana 502 on the first skips the other two; `run()` then
persists the JSON unconditionally. Turso holds day N-1, the JSON holds day N,
routes prefer the DB, and no heartbeat explains it.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))


class TestTrinStateIsNullWithoutAReading:
    def test_no_ma10_is_not_neutral(self):
        import fetch_trin as trin

        assert trin.classify_state(None) is None

    def test_a_real_mid_range_reading_is_still_neutral(self):
        import fetch_trin as trin

        assert trin.classify_state(1.10) == "neutral"

    def test_the_payload_carries_the_null_state(self):
        import fetch_trin as trin

        current = trin._current([], [], [])
        assert current["ma10"] is None
        assert current["state"] is None


class TestCreditRegimeIsNullWithoutReturns:
    @pytest.mark.parametrize(
        "spx,hyg", [(None, 0.01), (0.01, None), (float("nan"), 0.01), (0.01, float("inf"))]
    )
    def test_a_missing_or_non_finite_return_has_no_regime(self, spx, hyg):
        import fetch_credit_spread as cs

        assert cs.classify_regime(spx, hyg) is None

    def test_the_real_regimes_are_unchanged(self):
        import fetch_credit_spread as cs

        assert cs.classify_regime(0.02, -0.01) == "divergent"
        assert cs.classify_regime(0.02, 0.01) == "coupled"
        assert cs.classify_regime(-0.02, -0.01) == "risk-off"
        assert cs.classify_regime(-0.02, 0.01) == "credit-lead"
        assert cs.classify_regime(0.0, 0.0) == "coupled"


class TestIeiHygRankIsNullOnADegenerateWindow:
    def test_a_single_distinct_ratio_has_no_percentile(self):
        import fetch_iei_hyg as iei

        assert iei.pct_rank(1.5, 1.5, 1.5) is None

    def test_it_agrees_with_classify_state(self):
        import fetch_iei_hyg as iei

        assert iei.classify_state(1.5, 1.5, 1.5) == "neutral"
        assert iei.pct_rank(1.5, 1.5, 1.5) is None

    def test_a_real_window_still_ranks(self):
        import fetch_iei_hyg as iei

        assert iei.pct_rank(1.5, 1.0, 2.0) == 0.5
        assert iei.pct_rank(1.0, 1.0, 2.0) == 0.0


class TestIvrankPctAgreesWithRank:
    def test_a_degenerate_window_has_no_percentile(self):
        import fetch_ivrank as iv

        window = [0.12] * 252
        assert iv.rank_window(window) is None
        assert iv.pct_window(window) is None

    def test_a_real_window_still_ranks(self):
        import fetch_ivrank as iv

        window = [0.10, 0.12, 0.16, 0.14]
        assert iv.rank_window(window) is not None
        assert iv.pct_window(window) is not None


class TestFailedRowUpsertIsNotSuccess:
    def test_a_failed_row_write_still_heartbeats_error(self, monkeypatch):
        import fetch_ivrank as iv

        recorded: list[tuple] = []

        class _Writer:
            @staticmethod
            def ensure_no_replica_for_writers():
                return None

            @staticmethod
            def upsert_ivrank_rows(*_a, **_k):
                raise RuntimeError("hrana 502: stream not found")

            @staticmethod
            def upsert_scan_snapshot(*_a, **_k):
                return None

            @staticmethod
            def record_service_health(service, state, **kw):
                recorded.append((service, state, kw.get("error")))

        monkeypatch.setattr(iv, "writer", _Writer, raising=False)
        iv._write_db(
            {"scan_time": "2026-08-23T12:00:00Z"},
            "2026-08-23T12:00:00Z",
            rows_changed=True,
            rows=[{"date": "2026-08-22", "iv": 0.12, "source": "ib"}],
        )

        assert recorded, "the heartbeat was skipped by the same except block"
        assert recorded[-1][1] == "error"
