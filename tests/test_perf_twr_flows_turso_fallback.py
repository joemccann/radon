"""A Flex flows outage must not blank the performance page.

Incident 2026-08-17. `/performance` oscillated between publishing +90.81% and
publishing nothing at all. Whenever a run reached Flex it wrote `status=ok`;
whenever Flex errored it wrote `status=degraded` with `flows_status=failed`,
and the render layer correctly suppressed every gated metric — TWR, Max DD,
Sharpe, the equity curve. Same account, same NAV, five minutes apart.

The asymmetry: NAV degrades through a ladder (`flex -> disk_cache -> turso`)
but flows had NO fallback. One `fetch_flex_xml` exception went straight to
`FlowSet.failed`, which by design forbids publishing a TWR.

That is the right rule for flows we have never seen. It is the wrong rule when
the builder already mirrored a good flow set into Turso `external_flows` on a
previous run — those flows are facts about closed sessions and do not change
when IBKR's statement generator is unavailable. Serving last-known-good flows
with an honest `source` beats blanking a page that was correct minutes ago.

Observed codes driving this: 1001 ("Statement could not be generated at this
time") and 1025 ("Too many failed attempts. Please review your configuration").

No network, no Turso: the loader is monkeypatched on every path.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import scripts.perf_twr_builder as builder  # noqa: E402
from scripts.lib.twr_math import FlowsStatus  # noqa: E402

# Two real external flows already mirrored to Turso by an earlier good run.
MIRRORED_FLOWS = {"2026-01-13": 80_007.13, "2026-02-06": 655_497.16}


@pytest.fixture
def flex_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IB_FLEX_TOKEN", "test-token")
    monkeypatch.setenv("IB_FLEX_NAV_QUERY_ID", "1442520")


def _flex_raises(code: str):
    def _boom(*_args, **_kwargs):
        raise RuntimeError(
            f"Flex SendRequest failed code={code}: synthetic outage for test"
        )

    return _boom


# T-282: EVERY production caller passes `allow_fetch=False`.
# `build_and_persist` is the only non-test caller of `resolve_flows`
# (`perf_twr_builder.py:1793`) and passes
# `allow_fetch=bool(sendrequest) and not from_file` — false for the flex-pull
# file ingest and false for the weekday timer — while `POST /performance` and
# `/performance/background` are hard 404s and `get_external_flows_for_nav` has
# zero non-test callers. So the whole class below used to verify the 2026-08-17
# incident's contract exclusively through `allow_fetch=True`, a branch
# production never enters. Every case is now parametrized across both, and the
# `allow_fetch=False` leg reaches the fallback through
# `_flows_after_fetch_failure("file_ingest_no_fetch")` (`:812`) instead of
# through a fetch exception. Same contract, the shipped path.
_ALLOW_FETCH = pytest.mark.parametrize(
    "allow_fetch", [True, False], ids=["fetch_enabled", "file_ingest_no_fetch"]
)


@_ALLOW_FETCH
class TestFlowsFallBackToTursoOnFlexOutage:
    @pytest.mark.parametrize("code", ["1001", "1025"])
    def test_mirrored_flows_are_served_when_flex_errors(
        self, flex_configured, monkeypatch, code, allow_fetch
    ):
        """The exact incident: Flex is down, Turso has the flows, publish them."""
        monkeypatch.setattr(builder, "fetch_flex_xml", _flex_raises(code))
        monkeypatch.setattr(builder, "load_flows_from_turso", lambda: dict(MIRRORED_FLOWS))

        flows, _coverage = builder.resolve_flows(allow_fetch=allow_fetch)

        assert flows.status is not FlowsStatus.FAILED, (
            "a Flex outage with good mirrored flows must not suppress TWR"
        )
        assert dict(flows.by_date) == MIRRORED_FLOWS
        assert flows.source == "turso"

    def test_source_is_honest_so_the_page_can_say_where_flows_came_from(
        self, flex_configured, monkeypatch, allow_fetch
    ):
        monkeypatch.setattr(builder, "fetch_flex_xml", _flex_raises("1025"))
        monkeypatch.setattr(builder, "load_flows_from_turso", lambda: dict(MIRRORED_FLOWS))

        flows, _coverage = builder.resolve_flows(allow_fetch=allow_fetch)

        assert flows.source == "turso", "never claim a live Flex fetch"

    def test_still_fails_when_turso_has_nothing(
        self, flex_configured, monkeypatch, allow_fetch
    ):
        """No fallback data = the original rule. Never invent a zero flow set:
        treating 'unknown' as 'no deposits' is what produced +951% TWR."""
        monkeypatch.setattr(builder, "fetch_flex_xml", _flex_raises("1001"))
        monkeypatch.setattr(builder, "load_flows_from_turso", lambda: None)

        flows, _coverage = builder.resolve_flows(allow_fetch=allow_fetch)

        assert flows.status is FlowsStatus.FAILED

    def test_empty_turso_table_is_not_a_verified_zero(
        self, flex_configured, monkeypatch, allow_fetch
    ):
        """An empty dict is absence of evidence, not evidence of no flows."""
        monkeypatch.setattr(builder, "fetch_flex_xml", _flex_raises("1025"))
        monkeypatch.setattr(builder, "load_flows_from_turso", lambda: {})

        flows, _coverage = builder.resolve_flows(allow_fetch=allow_fetch)

        assert flows.status is FlowsStatus.FAILED


class TestTheHealthyPathNeverConsultsTurso:
    """The fallback must not shadow a healthy resolution, on either branch."""

    def test_a_live_flex_success_never_consults_turso(self, flex_configured, monkeypatch):
        called = {"turso": False}

        def _turso():
            called["turso"] = True
            return dict(MIRRORED_FLOWS)

        monkeypatch.setattr(builder, "fetch_flex_xml", lambda *a, **k: "<FlexQueryResponse/>")
        monkeypatch.setattr(builder, "load_flows_from_turso", _turso)
        monkeypatch.setattr(
            builder, "parse_flows", lambda _xml: builder.FlowSet.empty_verified("flex")
        )

        flows, _coverage = builder.resolve_flows()

        assert called["turso"] is False
        assert flows.source == "flex"

    def test_a_file_ingest_statement_never_consults_turso_and_never_fetches(
        self, flex_configured, monkeypatch
    ):
        """The shipped healthy path: allow_fetch=False with a statement already
        in hand. An Activity statement carries CashTransaction + Transfers, so
        it must be parsed directly — no SendRequest against a throttled token,
        and no fallback shadowing perfectly good flows."""
        called = {"turso": False, "fetch": False}

        def _turso():
            called["turso"] = True
            return dict(MIRRORED_FLOWS)

        def _fetch(*_a, **_k):
            called["fetch"] = True
            raise AssertionError("allow_fetch=False must never SendRequest")

        monkeypatch.setattr(builder, "fetch_flex_xml", _fetch)
        monkeypatch.setattr(builder, "load_flows_from_turso", _turso)
        monkeypatch.setattr(
            builder, "parse_flows", lambda _xml: builder.FlowSet.empty_verified("flex")
        )

        document = builder.FlexDocument(query_id="1442520", xml="<FlexQueryResponse/>")
        flows, _coverage = builder.resolve_flows(document, allow_fetch=False)

        assert called == {"turso": False, "fetch": False}
        assert flows.source == "flex"

    def test_file_ingest_without_a_statement_falls_back_rather_than_fetching(
        self, flex_configured, monkeypatch
    ):
        """The branch production actually takes when the pulled file is a Trade
        Confirmation with no flows section: no document xml, fetching
        forbidden. It must degrade to the Turso mirror, not SendRequest and not
        blank the page."""
        called = {"fetch": False}

        def _fetch(*_a, **_k):
            called["fetch"] = True
            raise AssertionError("allow_fetch=False must never SendRequest")

        monkeypatch.setattr(builder, "fetch_flex_xml", _fetch)
        monkeypatch.setattr(builder, "load_flows_from_turso", lambda: dict(MIRRORED_FLOWS))

        flows, _coverage = builder.resolve_flows(None, allow_fetch=False)

        assert called["fetch"] is False
        assert flows.status is not FlowsStatus.FAILED
        assert dict(flows.by_date) == MIRRORED_FLOWS
        assert flows.source == "turso"


# ── T-081: the real reader against a real 0035 schema ────────────────────
#
# Two writers share `external_flows` with distinct PK `flow_type` values:
# `migrate_perf_twr.py` backfills classified `deposit|withdrawal|acats` rows
# and the builder mirrors its own subperiod `c` as `external`. Both describe
# the SAME cash movement, so a date carrying both must count once.

import sqlite3  # noqa: E402

MIGRATION_0035 = REPO_ROOT / "scripts" / "db" / "migrations" / "0035_perf_twr.sql"
DEPOSIT_DATE = "2026-01-13"
DEPOSIT_AMOUNT = 80_007.13


def _sqlite_with_0035() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT)")
    db.executescript(MIGRATION_0035.read_text())
    return db


def _insert_flow(
    db: sqlite3.Connection,
    report_date: str,
    amount: float,
    flow_type: str,
    account_id: str = "U1",
) -> None:
    db.execute(
        "INSERT INTO external_flows (account_id, report_date, amount, flow_type, note) "
        "VALUES (?, ?, ?, ?, ?)",
        (account_id, report_date, amount, flow_type, flow_type),
    )


@pytest.fixture
def turso_is_sqlite(monkeypatch: pytest.MonkeyPatch) -> sqlite3.Connection:
    """Point the builder's Turso seam at an in-memory sqlite with 0035 applied."""
    db = _sqlite_with_0035()
    monkeypatch.setenv("TURSO_DB_URL", "libsql://test")
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "test")
    import scripts.db.client as scripts_client

    monkeypatch.setattr(scripts_client, "get_db", lambda: db)
    try:
        import db.client as bare_client  # type: ignore

        monkeypatch.setattr(bare_client, "get_db", lambda: db)
    except ImportError:
        pass
    return db


class TestLoadFlowsFromTursoCountsAMirroredRowOnce:
    def test_deposit_plus_mirrored_external_row_counts_once(self, turso_is_sqlite):
        """Backfilled `deposit` + builder-mirrored `external` on one date is ONE deposit."""
        _insert_flow(turso_is_sqlite, DEPOSIT_DATE, DEPOSIT_AMOUNT, "deposit")
        _insert_flow(turso_is_sqlite, DEPOSIT_DATE, DEPOSIT_AMOUNT, "external")

        flows = builder.load_flows_from_turso()

        assert flows == {DEPOSIT_DATE: pytest.approx(DEPOSIT_AMOUNT)}

    def test_classified_rows_on_one_date_still_net(self, turso_is_sqlite):
        """A deposit and a withdrawal on the same session net; that meaning stays."""
        _insert_flow(turso_is_sqlite, DEPOSIT_DATE, DEPOSIT_AMOUNT, "deposit")
        _insert_flow(turso_is_sqlite, DEPOSIT_DATE, -5_000.0, "withdrawal")
        _insert_flow(turso_is_sqlite, "2026-02-06", 655_497.16, "acats")

        flows = builder.load_flows_from_turso()

        assert flows == {
            DEPOSIT_DATE: pytest.approx(DEPOSIT_AMOUNT - 5_000.0),
            "2026-02-06": pytest.approx(655_497.16),
        }

    def test_external_only_date_is_still_served(self, turso_is_sqlite):
        """A date the builder alone mirrored has no classified twin; keep it."""
        _insert_flow(turso_is_sqlite, "2026-02-06", 655_497.16, "external")

        assert builder.load_flows_from_turso() == {"2026-02-06": pytest.approx(655_497.16)}


class TestPrecedenceIsPerAccountNotPerDate:
    """`external_flows` is keyed per account, and so is the mirror/classified pair.

    `_statements` (`perf_twr_builder.py:341`) already fans a multi-statement Flex
    document out per `accountId`, and the backfill falls back to the literal
    `"ALL"` where the element carries none, so one date genuinely can hold rows
    under two account ids. Collapsing precedence to the date alone drops a
    second account's flow entirely instead of counting it once.
    """

    def test_second_account_mirror_is_not_swallowed_by_the_first_classified(
        self, turso_is_sqlite
    ):
        _insert_flow(turso_is_sqlite, "2026-05-01", 10_000.0, "deposit", account_id="ALL")
        _insert_flow(turso_is_sqlite, "2026-05-01", 7_500.0, "external", account_id="U123")

        assert builder.load_flows_from_turso() == {"2026-05-01": pytest.approx(17_500.0)}

    def test_each_account_still_dedupes_its_own_mirror(self, turso_is_sqlite):
        _insert_flow(turso_is_sqlite, "2026-04-01", 10_000.0, "deposit", account_id="U123")
        _insert_flow(turso_is_sqlite, "2026-04-01", 10_000.0, "external", account_id="U123")
        _insert_flow(turso_is_sqlite, "2026-04-01", 2_500.0, "deposit", account_id="U999")

        assert builder.load_flows_from_turso() == {"2026-04-01": pytest.approx(12_500.0)}
