"""The backfill ladder comment names the rungs in the order the code runs them.

Audit implicit-state-machines-20261103 §3.4 "Data-source ladders per script":
the section header said IB -> UW -> RH -> Yahoo while
``_fetch_closes_via_ladder`` runs IB, then Robinhood, then UW, then Yahoo
(root CLAUDE.md data-source priority).
"""
import inspect
import re
from pathlib import Path

import portfolio_risk

SOURCE = Path(portfolio_risk.__file__).read_text()


def test_header_matches_the_ladder_order():
    body = inspect.getsource(portfolio_risk._fetch_closes_via_ladder)
    calls = re.findall(r"_fetch_(ib|rh|uw|yahoo)_closes\(", body)
    order = [{"ib": "IB", "rh": "RH", "uw": "UW", "yahoo": "Yahoo"}[c] for c in calls]
    assert order == ["IB", "RH", "UW", "Yahoo"]
    assert f"# ── Backfill ladder ({' -> '.join(order)}, persisted to Turso)" in SOURCE
