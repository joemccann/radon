"""The screenshot, end to end: Flex down, TWR blank, four `--` tiles.

Incident 2026-08-17/18. `/performance` rendered DEGRADED MEASUREMENT with
TWR TOTAL, ANNUALIZED TWR, MAX DD and SHARPE all showing `--` and the reason
"external flows unavailable", while NAV itself was fine (disk_cache, as of
2026-08-14) and Turso already held every flow for the period.

With the mirror fallback in place the same outage must publish a real TWR and
say plainly where the flows came from.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import scripts.perf_twr_builder as builder  # noqa: E402

NAV_QUERY_ID = "1442520"


@pytest.fixture
def flex_outage_with_mirror(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IB_FLEX_TOKEN", "test-token")
    monkeypatch.setenv("IB_FLEX_NAV_QUERY_ID", NAV_QUERY_ID)
    monkeypatch.delenv("IB_FLEX_FLOWS_QUERY_ID", raising=False)

    def _boom(*_args, **_kwargs):
        raise RuntimeError(
            "Flex SendRequest failed code=1025: Too many failed attempts. "
            "Please review your configuration."
        )

    nav = {
        "2026-08-10": 1_000_000.0,
        "2026-08-11": 1_010_000.0,
        "2026-08-12": 1_020_000.0,
        "2026-08-13": 950_000.0,
        "2026-08-14": 960_000.0,
    }
    monkeypatch.setattr(builder, "fetch_flex_xml", _boom)
    monkeypatch.setattr(builder, "load_nav_from_disk", lambda: dict(nav))
    monkeypatch.setattr(builder, "load_flows_from_turso", lambda: {"2026-08-13": -80_000.0})
    monkeypatch.setattr(builder, "load_flows_coverage_through", lambda: "2026-08-14")
    monkeypatch.setattr(builder, "load_benchmark_closes", lambda *a, **k: {})
    monkeypatch.setattr(builder, "get_risk_free_rate", lambda **k: (0.0, ""))


def test_twr_is_published_instead_of_suppressed(flex_outage_with_mirror):
    payload = builder.build_and_persist(persist=False)

    assert payload["twr"] is not None, "a Flex outage still blanked the page"
    assert payload["twr"]["cum_return"] is not None


def test_the_withdrawal_is_not_read_as_a_negative_return(flex_outage_with_mirror):
    """-80k out of 1.02M on 08-13 is a withdrawal, not a -6.9% day."""
    payload = builder.build_and_persist(persist=False)

    assert payload["twr"]["cum_return"] > 0


def test_provenance_is_declared(flex_outage_with_mirror):
    payload = builder.build_and_persist(persist=False)

    codes = {w["code"] for w in payload["warnings"]}
    assert "FLOWS_SOURCE_MIRROR" in codes
    assert "FLOWS_FETCH_FAILED" not in codes


def test_serving_a_mirror_does_not_by_itself_gate_the_page(flex_outage_with_mirror):
    """Provenance is declared in a warning, not paid for by degrading status.

    `warn` floors the payload to "stale" and the render layer gates the TWR on
    "stale" exactly as on "degraded" (#52) — so flagging the mirror at `warn`
    would blank the very page this fallback exists to keep alive.
    """
    payload = builder.build_and_persist(persist=False)

    mirror = next(w for w in payload["warnings"] if w["code"] == "FLOWS_SOURCE_MIRROR")
    assert mirror["severity"] == "info"


def test_nav_past_mirror_coverage_is_not_chained(flex_outage_with_mirror, monkeypatch):
    """Coverage stops a session early: 08-14 must not be chained on no evidence."""
    monkeypatch.setattr(builder, "load_flows_coverage_through", lambda: "2026-08-13")

    payload = builder.build_and_persist(persist=False)

    mirror = next(w for w in payload["warnings"] if w["code"] == "FLOWS_SOURCE_MIRROR")
    assert mirror["context"]["sessions_dropped"] == 1
    assert mirror["context"]["covered_through"] == "2026-08-13"
