"""R-345 / REL-126: one flow number per session, and a disagreement is told.

The default no-fetch path (the unattended weekday invocation) derives flows
from `load_flows_from_turso`, which nets per `(account_id, report_date)` and
let classified rows OVERWRITE the builder's own mirror via
`per_account.update(classified)`. A `--from-file` run parses CashTransaction +
Transfers directly. A day carrying both a deposit and an ACATS transfer nets
differently under the two, so the same session's subperiod flow — and every
downstream `cum_return` — changed retroactively depending on which invocation
last wrote performance.json. Both published `status: ok`.
"""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import perf_twr_builder as ptb  # noqa: E402


def _rows(*rows):
    return [
        {"account_id": a, "report_date": d, "amount": amt, "flow_type": t}
        for a, d, amt, t in rows
    ]


class TestDisagreementIsRecorded:
    def test_a_mirror_that_disagrees_with_the_classified_net_is_flagged(self, monkeypatch):
        # The mirror saw $80k deposit + $45k ACATS; the backfill classified
        # only the deposit. Precedence hands the session to the classified
        # rows, so the ACATS silently vanishes from that session's flow.
        monkeypatch.setattr(
            ptb, "_query_turso",
            lambda _sql: _rows(
                ("U1", "2026-08-12", 125000.0, ptb._MIRRORED_FLOW_TYPE),
                ("U1", "2026-08-12", 80000.0, "deposit"),
            ),
        )
        flows = ptb.load_flows_from_turso()
        assert flows == {"2026-08-12": 80000.0}, "precedence itself is unchanged"

        divergences = ptb.flow_source_divergences()
        assert ("U1", "2026-08-12") in divergences
        assert divergences[("U1", "2026-08-12")] == (125000.0, 80000.0)

    def test_the_divergence_raises_a_warning_that_floors_the_status(self, monkeypatch):
        monkeypatch.setattr(
            ptb, "_query_turso",
            lambda _sql: _rows(
                ("U1", "2026-08-12", 125000.0, ptb._MIRRORED_FLOW_TYPE),
                ("U1", "2026-08-12", 80000.0, "deposit"),
            ),
        )
        ptb.load_flows_from_turso()
        warnings = ptb.flow_divergence_warnings()
        assert [w["code"] for w in warnings] == ["FLOWS_SOURCE_DISAGREEMENT"]
        assert warnings[0]["severity"] == "warn", (
            "`warn` floors the payload to stale, which is the honest state "
            "when the same session nets two ways"
        )
        assert warnings[0]["context"]["report_date"] == "2026-08-12"

    def test_agreeing_sources_raise_nothing(self, monkeypatch):
        monkeypatch.setattr(
            ptb, "_query_turso",
            lambda _sql: _rows(
                ("U1", "2026-08-12", 80000.0, ptb._MIRRORED_FLOW_TYPE),
                ("U1", "2026-08-12", 80000.0, "deposit"),
            ),
        )
        assert ptb.load_flows_from_turso() == {"2026-08-12": 80000.0}
        assert ptb.flow_divergence_warnings() == []

    def test_a_classified_only_session_raises_nothing(self, monkeypatch):
        monkeypatch.setattr(
            ptb, "_query_turso",
            lambda _sql: _rows(("U1", "2026-08-12", 80000.0, "deposit")),
        )
        assert ptb.load_flows_from_turso() == {"2026-08-12": 80000.0}
        assert ptb.flow_divergence_warnings() == []

    def test_a_second_account_is_not_collapsed_into_the_first(self, monkeypatch):
        """The PK is per account; deciding precedence on the date alone would
        drop a second account's flow (the pre-existing comment's point)."""
        monkeypatch.setattr(
            ptb, "_query_turso",
            lambda _sql: _rows(
                ("U1", "2026-08-12", 80000.0, "deposit"),
                ("U2", "2026-08-12", 20000.0, ptb._MIRRORED_FLOW_TYPE),
            ),
        )
        assert ptb.load_flows_from_turso() == {"2026-08-12": 100000.0}
        assert ptb.flow_divergence_warnings() == []

    def test_the_divergence_record_is_reset_between_loads(self, monkeypatch):
        monkeypatch.setattr(
            ptb, "_query_turso",
            lambda _sql: _rows(
                ("U1", "2026-08-12", 125000.0, ptb._MIRRORED_FLOW_TYPE),
                ("U1", "2026-08-12", 80000.0, "deposit"),
            ),
        )
        ptb.load_flows_from_turso()
        assert ptb.flow_divergence_warnings()

        monkeypatch.setattr(
            ptb, "_query_turso",
            lambda _sql: _rows(("U1", "2026-08-13", 100.0, "deposit")),
        )
        ptb.load_flows_from_turso()
        assert ptb.flow_divergence_warnings() == [], (
            "a stale divergence would keep flooring the status forever"
        )
