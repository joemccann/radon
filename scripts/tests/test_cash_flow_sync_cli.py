"""Operator recovery path + typed exit codes for scripts/cash_flow_sync.py.

Plan items covered (docs/cash-flow-sync-overhaul.md):

  C10 — `--from-file` / `--dry-run` / `--since`. The operator hit a Flex
        throttle embargo twice on 2026-08-16 and had to hand-copy the parse
        loop into a scratch script to ingest a manual export. Replaying a
        saved statement must be a first-class CLI mode that works with no
        credentials in the environment at all.
  C3  — one exit code per failure class, so the daemon handler stops
        classifying failures by substring-matching the last three lines of
        stderr.
  C16 — record and assert statement SHAPE (statement count, account,
        period, levelOfDetail, Transfers section, currencies). Whether
        those sections appear is a checkbox in the IBKR query builder and
        the difference silently halves or doubles every downstream total.
  C17 — a Turso write failure must NOT be launderable into another Flex
        SendRequest.

⛔ Every test here is offline. The account sits at throttle_count 3 of 4 on
a sliding-window rate limit; one stray SendRequest costs a week of sync.
`urlopen` is patched to raise on every path that must not touch the network.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import cash_flow_sync

FIXTURES = Path(__file__).resolve().parent / "fixtures"
LEGACY_FIXTURE = FIXTURES / "cash_transactions_flex_sample.xml"
YTD_FIXTURE = FIXTURES / "cash_transactions_flex_ytd_detail_sample.xml"


class _ExplodingUrlopen:
    """Any call is a Flex request. Under the current embargo that is a
    week-long outage, so it must be impossible, not merely unlikely."""

    def __call__(self, *args, **kwargs):  # pragma: no cover — must never run
        raise AssertionError(
            "cash_flow_sync opened a Flex socket on an offline code path"
        )


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cash_flow_sync, "urlopen", _ExplodingUrlopen())


@pytest.fixture
def no_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """`--from-file` must work on a laptop with no Flex token at all."""
    monkeypatch.delenv("IB_FLEX_TOKEN", raising=False)
    monkeypatch.delenv("IB_FLEX_NAV_QUERY_ID", raising=False)


class _CapturingWriter:
    """Stand-in for db.writer.upsert_cash_flow_rows."""

    def __init__(self, fail_on_call: int | None = None) -> None:
        self.calls: list[list[dict]] = []
        self._fail_on_call = fail_on_call

    def __call__(self, rows, **kwargs):
        self.calls.append(list(rows))
        if self._fail_on_call is not None and len(self.calls) >= self._fail_on_call:
            raise RuntimeError("upstream forward failed (502)")
        return len(rows)

    @property
    def written(self) -> list[dict]:
        return [row for call in self.calls for row in call]


@pytest.fixture
def writer(monkeypatch: pytest.MonkeyPatch) -> _CapturingWriter:
    import db.writer as writer_mod

    stub = _CapturingWriter()
    monkeypatch.setattr(writer_mod, "upsert_cash_flow_rows", stub, raising=False)
    monkeypatch.setattr(
        cash_flow_sync,
        "_load_existing_cash_flow_ids",
        lambda: set(),
    )
    return stub


def _run(*argv: str) -> tuple[int, str, str]:
    """Invoke main() with argv, returning (exit_code, stdout, stderr)."""
    import io
    import contextlib

    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cash_flow_sync.main(list(argv))
    return code, out.getvalue(), err.getvalue()


def _status_line(stdout: str) -> dict:
    """The machine-readable last line the daemon handler branches on."""
    return json.loads(stdout.strip().splitlines()[-1])


# ── C10: operator recovery from a saved statement ──────────────────────

class TestFromFile:
    """Ingest a manual export with zero credentials and zero network."""

    def test_writes_every_parsed_row(self, no_network, no_credentials, writer):
        code, stdout, _ = _run("--from-file", str(LEGACY_FIXTURE), "--no-file")

        assert code == cash_flow_sync.EXIT_OK
        # 20 CashTransaction elements - 3 with no transactionID - 1 zero-amount.
        assert len(writer.written) == 16
        assert _status_line(stdout)["status"] == "ok"

    def test_works_with_no_flex_credentials_present(
        self, no_network, no_credentials, writer
    ):
        """The env guard must sit inside the FETCH branch. Today it runs
        before argument dispatch, so `--from-file` on a laptop exits 1."""
        assert "IB_FLEX_TOKEN" not in cash_flow_sync.os.environ
        code, _, _ = _run("--from-file", str(LEGACY_FIXTURE), "--no-file")
        assert code == cash_flow_sync.EXIT_OK

    def test_missing_file_is_a_config_error_not_a_flex_error(
        self, no_network, no_credentials, writer
    ):
        code, stdout, _ = _run("--from-file", str(FIXTURES / "nope.xml"), "--no-file")
        assert code == cash_flow_sync.EXIT_CONFIG_ERROR
        assert _status_line(stdout)["class"] == "config_error"
        assert writer.calls == []

    def test_json_mode_prints_rows_and_writes_nothing(
        self, no_network, no_credentials, writer
    ):
        code, stdout, _ = _run("--from-file", str(LEGACY_FIXTURE), "--json")
        assert code == cash_flow_sync.EXIT_OK
        assert len(json.loads(stdout)) == 16
        assert writer.calls == []

    def test_ytd_detail_export_parses(self, no_network, no_credentials, writer):
        """The current query shape: 3 DETAIL rows, none id-less."""
        code, _, _ = _run("--from-file", str(YTD_FIXTURE), "--no-file")
        assert code == cash_flow_sync.EXIT_OK
        assert [r["id"] for r in writer.written] == [
            "37134609475", "37155398573", "37308647311",
        ]
        assert [r["type"] for r in writer.written] == ["Fee", "Interest", "Deposit"]


class TestDryRun:
    def test_writes_nothing(self, no_network, no_credentials, writer):
        code, stdout, _ = _run("--from-file", str(LEGACY_FIXTURE), "--dry-run")
        assert code == cash_flow_sync.EXIT_OK
        assert writer.calls == []
        status = _status_line(stdout)
        assert status["status"] == "ok"
        assert status["dry_run"] is True
        assert status["rows"] == 16

    def test_reports_which_rows_are_new(self, no_network, no_credentials, monkeypatch):
        """Only 41191444701 (the reused-id interest trio) is already stored,
        so 16 parsed - 3 known = 13 new ids."""
        import db.writer as writer_mod

        exploding = MagicMock(side_effect=AssertionError("dry-run must not write"))
        monkeypatch.setattr(writer_mod, "upsert_cash_flow_rows", exploding, raising=False)
        monkeypatch.setattr(
            cash_flow_sync, "_load_existing_cash_flow_ids", lambda: {"41191444701"}
        )

        _, stdout, _ = _run("--from-file", str(LEGACY_FIXTURE), "--dry-run")
        assert _status_line(stdout)["new_rows"] == 13

    def test_unreadable_baseline_does_not_fail_the_run(
        self, no_network, no_credentials, monkeypatch
    ):
        def boom():
            raise RuntimeError("stream not found")

        monkeypatch.setattr(cash_flow_sync, "_load_existing_cash_flow_ids", boom)
        code, stdout, _ = _run("--from-file", str(LEGACY_FIXTURE), "--dry-run")
        assert code == cash_flow_sync.EXIT_OK
        assert _status_line(stdout)["new_rows"] is None


class TestSince:
    def test_bounds_the_write_set(self, no_network, no_credentials, writer):
        """Hand-counted from the fixture: 2026-06-24 carries 4 rows (two -1
        fees, -42,000 and -60,000), 2026-07-06 carries 3 (the reused-id
        interest trio) and 2026-07-08 carries 1. 4 + 3 + 1 = 8."""
        code, _, _ = _run(
            "--from-file", str(LEGACY_FIXTURE), "--since", "2026-06-01", "--no-file"
        )
        assert code == cash_flow_sync.EXIT_OK
        assert len(writer.written) == 8
        assert min(r["date"] for r in writer.written) == "2026-06-24"

    def test_since_after_every_row_writes_nothing(
        self, no_network, no_credentials, writer
    ):
        code, stdout, _ = _run(
            "--from-file", str(LEGACY_FIXTURE), "--since", "2027-01-01", "--no-file"
        )
        assert code == cash_flow_sync.EXIT_OK
        assert writer.written == []
        assert _status_line(stdout)["rows"] == 0


# ── C16: statement shape is data, not an assumption ────────────────────

class TestStatementShape:
    def test_legacy_365_day_shape(self):
        shape = cash_flow_sync.describe_statement_shape(
            LEGACY_FIXTURE.read_text(encoding="utf-8")
        )
        assert shape["flex_statement_count"] == 1
        assert shape["account_ids"] == ["U0000000"]
        assert shape["period_from"] == "2025-08-15"
        assert shape["period_to"] == "2026-08-14"
        assert shape["when_generated"] == "20260816;123413"
        assert shape["cash_transaction_count"] == 20
        assert shape["skipped_no_transaction_id"] == 3
        assert shape["skipped_zero_amount"] == 1
        assert shape["parsed_rows"] == 16
        assert shape["levels_of_detail"] == []
        assert shape["has_transfers_section"] is False
        assert shape["transfer_count"] == 0
        assert shape["currencies"] == ["USD"]
        assert shape["non_usd_rows"] == 0

    def test_ytd_detail_shape(self):
        shape = cash_flow_sync.describe_statement_shape(
            YTD_FIXTURE.read_text(encoding="utf-8")
        )
        assert shape["flex_statement_count"] == 1
        assert shape["period_from"] == "2026-01-01"
        assert shape["period_to"] == "2026-08-14"
        assert shape["cash_transaction_count"] == 3
        assert shape["skipped_no_transaction_id"] == 0
        assert shape["skipped_zero_amount"] == 0
        assert shape["parsed_rows"] == 3
        assert shape["levels_of_detail"] == ["DETAIL"]
        assert shape["has_transfers_section"] is True
        assert shape["transfer_count"] == 2

    def test_the_two_real_exports_differ_only_where_expected(self):
        """The double-count bug is a function of query CONFIG, not code."""
        legacy = cash_flow_sync.describe_statement_shape(
            LEGACY_FIXTURE.read_text(encoding="utf-8")
        )
        ytd = cash_flow_sync.describe_statement_shape(
            YTD_FIXTURE.read_text(encoding="utf-8")
        )
        differing = {k for k in legacy if legacy[k] != ytd[k]}
        assert differing == {
            "period_from",
            "when_generated",
            "cash_transaction_count",
            "skipped_no_transaction_id",
            "skipped_zero_amount",
            "parsed_rows",
            "levels_of_detail",
            "has_transfers_section",
            "transfer_count",
        }

    def test_legacy_shape_warns_about_the_summary_row_proxy(self):
        shape = cash_flow_sync.describe_statement_shape(
            LEGACY_FIXTURE.read_text(encoding="utf-8")
        )
        warnings = cash_flow_sync.statement_shape_warnings(shape)
        assert any("levelOfDetail" in w for w in warnings)

    def test_ytd_shape_warns_that_transfers_are_not_ingested(self):
        shape = cash_flow_sync.describe_statement_shape(
            YTD_FIXTURE.read_text(encoding="utf-8")
        )
        warnings = cash_flow_sync.statement_shape_warnings(shape)
        assert any("Transfer" in w for w in warnings)

    def test_non_usd_row_is_flagged_not_silently_summed(self):
        mixed = (
            "<FlexQueryResponse><FlexStatements count='1'>"
            "<FlexStatement accountId='U0000000' fromDate='20260101' toDate='20260814'>"
            "<CashTransactions>"
            "<CashTransaction currency='USD' amount='10' type='Other Fees'"
            " transactionID='1' reportDate='20260105' levelOfDetail='DETAIL' />"
            "<CashTransaction currency='EUR' amount='20' type='Other Fees'"
            " transactionID='2' reportDate='20260106' levelOfDetail='DETAIL' />"
            "</CashTransactions></FlexStatement></FlexStatements></FlexQueryResponse>"
        )
        shape = cash_flow_sync.describe_statement_shape(mixed)
        assert shape["currencies"] == ["EUR", "USD"]
        assert shape["non_usd_rows"] == 1
        assert any("EUR" in w for w in cash_flow_sync.statement_shape_warnings(shape))

    def test_two_flex_statements_is_a_shape_warning(self):
        doubled = (
            "<FlexQueryResponse><FlexStatements count='2'>"
            "<FlexStatement accountId='U0000000'><CashTransactions/></FlexStatement>"
            "<FlexStatement accountId='U1111111'><CashTransactions/></FlexStatement>"
            "</FlexStatements></FlexQueryResponse>"
        )
        shape = cash_flow_sync.describe_statement_shape(doubled)
        assert shape["flex_statement_count"] == 2
        assert shape["account_ids"] == ["U0000000", "U1111111"]
        warnings = cash_flow_sync.statement_shape_warnings(shape)
        assert any("FlexStatement" in w for w in warnings)

    def test_shape_is_reported_on_the_status_line(
        self, no_network, no_credentials, writer
    ):
        _, stdout, _ = _run("--from-file", str(YTD_FIXTURE), "--no-file")
        status = _status_line(stdout)
        assert status["shape"]["transfer_count"] == 2
        assert status["shape_warnings"]


# ── C1: one poll budget, derived not duplicated ────────────────────────

class TestPollBudget:
    def test_delays_stay_inside_the_declared_budget(self):
        delays = cash_flow_sync.poll_delays(cash_flow_sync.FLEX_POLL_BUDGET_SECONDS)
        assert sum(delays) <= cash_flow_sync.FLEX_POLL_BUDGET_SECONDS
        # One more poll at the capped sleep would overrun it.
        assert sum(delays) + cash_flow_sync.MAX_POLL_SLEEP_SECONDS > (
            cash_flow_sync.FLEX_POLL_BUDGET_SECONDS
        )

    def test_backoff_is_capped_exponential(self):
        assert cash_flow_sync.poll_delays(30)[:4] == [2.0, 4.0, 8.0, 15.0]

    def test_budget_covers_the_observed_eod_generation_latency(self):
        """IBKR takes 2.5-3.5 min to generate this query around 17:00 ET."""
        assert cash_flow_sync.FLEX_POLL_BUDGET_SECONDS >= 300


# ── C3: typed exit codes, not stderr substrings ────────────────────────

def _xml_response(body: str) -> MagicMock:
    resp = MagicMock()
    resp.read.return_value = body.encode("utf-8")
    return resp


_SEND_OK = (
    "<FlexStatementResponse><Status>Success</Status>"
    "<ReferenceCode>1234567890</ReferenceCode></FlexStatementResponse>"
)


@pytest.fixture
def credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IB_FLEX_TOKEN", "test-token")
    monkeypatch.setenv("IB_FLEX_NAV_QUERY_ID", "1442520")


class TestExitCodes:
    """The handler must branch on returncode, not on the tail of stderr."""

    def test_success_is_zero(self, credentials, writer, monkeypatch):
        statement = LEGACY_FIXTURE.read_text(encoding="utf-8")
        with patch.object(cash_flow_sync, "urlopen") as mock_urlopen, \
             patch.object(cash_flow_sync.time, "sleep"):
            mock_urlopen.side_effect = [_xml_response(_SEND_OK), _xml_response(statement)]
            code, stdout, _ = _run("--no-file")
        assert code == cash_flow_sync.EXIT_OK
        assert _status_line(stdout)["class"] == "ok"

    def test_only_1018_takes_the_breaker_lane(self, credentials, writer):
        """1018 is the ONLY documented rate limit, so it is the only code that
        may reach the 24h/48h/72h/168h ladder."""
        body = (
            "<FlexStatementResponse><Status>Fail</Status>"
            "<ErrorCode>1018</ErrorCode>"
            "<ErrorMessage>Too many requests</ErrorMessage></FlexStatementResponse>"
        )
        with patch.object(cash_flow_sync, "urlopen") as mock_urlopen, \
             patch.object(cash_flow_sync.time, "sleep"):
            mock_urlopen.side_effect = [_xml_response(body)]
            code, stdout, _ = _run("--no-file")

        assert code == cash_flow_sync.EXIT_THROTTLE
        status = _status_line(stdout)
        assert status["class"] == "throttle"
        assert status["code"] == "1018"

    @pytest.mark.parametrize("transient_code", ["1001", "1009", "1019"])
    def test_transient_codes_take_the_soft_lane_not_the_breaker(
        self, credentials, writer, transient_code
    ):
        """These used to exit 10 and escalate a ladder toward a week-long
        embargo. IBKR's own text for all three is "try again shortly", and 1019
        is literally "generation in progress". The soft lane retries in minutes;
        the breaker lane waits a day. That difference is the 10-day outage."""
        body = (
            "<FlexStatementResponse><Status>Fail</Status>"
            f"<ErrorCode>{transient_code}</ErrorCode>"
            "<ErrorMessage>Please try again shortly.</ErrorMessage>"
            "</FlexStatementResponse>"
        )
        with patch.object(cash_flow_sync, "urlopen") as mock_urlopen, \
             patch.object(cash_flow_sync.time, "sleep"):
            mock_urlopen.side_effect = [_xml_response(body)] * 4
            code, stdout, _ = _run("--no-file")

        assert code != cash_flow_sync.EXIT_THROTTLE
        assert code != cash_flow_sync.EXIT_FLEX_APP_ERROR  # not permanent either
        assert _status_line(stdout)["class"] != "throttle"

    def test_1025_lockout_exits_distinct_from_permanent_and_does_not_retry(
        self, credentials, writer
    ):
        """Production 2026-08-21: SendRequest 1025 was exit 11 (permanent),
        so the handler burned the day and scheduled Monday 08:00 ET — which
        is another SendRequest on a locked token. Lockout must not share
        that lane."""
        body = (
            "<FlexStatementResponse><Status>Fail</Status>"
            "<ErrorCode>1025</ErrorCode>"
            "<ErrorMessage>Too many failed attempts. Please review your "
            "configuration.</ErrorMessage></FlexStatementResponse>"
        )
        with patch.object(cash_flow_sync, "urlopen") as mock_urlopen, \
             patch.object(cash_flow_sync.time, "sleep") as mock_sleep, \
             patch.object(cash_flow_sync, "_record_token_lockout"):
            mock_urlopen.side_effect = [_xml_response(body)]
            code, stdout, _ = _run("--no-file")

        assert code == cash_flow_sync.EXIT_FLEX_LOCKOUT
        assert _status_line(stdout)["class"] == "lockout"
        assert _status_line(stdout)["code"] == "1025"
        assert mock_urlopen.call_count == 1
        mock_sleep.assert_not_called()

    def test_permanent_flex_error_exits_eleven(self, credentials, writer):
        body = (
            "<FlexStatementResponse><Status>Fail</Status>"
            "<ErrorCode>1012</ErrorCode>"
            "<ErrorMessage>Token has expired</ErrorMessage></FlexStatementResponse>"
        )
        with patch.object(cash_flow_sync, "urlopen") as mock_urlopen, \
             patch.object(cash_flow_sync.time, "sleep"):
            mock_urlopen.side_effect = [_xml_response(body)]
            code, stdout, _ = _run("--no-file")

        assert code == cash_flow_sync.EXIT_FLEX_APP_ERROR
        assert _status_line(stdout)["class"] == "flex_app_error"

    def test_statement_never_ready_exits_twelve(self, credentials, writer, monkeypatch):
        monkeypatch.setattr(cash_flow_sync, "FLEX_POLL_BUDGET_SECONDS", 20.0)
        not_ready = "<FlexStatementResponse><Status>Warn</Status></FlexStatementResponse>"
        with patch.object(cash_flow_sync, "urlopen") as mock_urlopen, \
             patch.object(cash_flow_sync.time, "sleep"):
            mock_urlopen.side_effect = [_xml_response(_SEND_OK)] + [
                _xml_response(not_ready) for _ in range(20)
            ]
            code, stdout, _ = _run("--no-file")

        assert code == cash_flow_sync.EXIT_STATEMENT_NOT_READY
        assert _status_line(stdout)["class"] == "not_ready"

    def test_malformed_statement_exits_thirteen(self, credentials, writer):
        torn = "<FlexStatements><CashTransaction amount='1'"
        with patch.object(cash_flow_sync, "urlopen") as mock_urlopen, \
             patch.object(cash_flow_sync.time, "sleep"):
            mock_urlopen.side_effect = [_xml_response(_SEND_OK), _xml_response(torn)]
            code, stdout, _ = _run("--no-file")

        assert code == cash_flow_sync.EXIT_PARSE_ERROR
        assert _status_line(stdout)["class"] == "parse_error"

    def test_missing_credentials_exits_config_error(self, no_credentials, writer):
        code, stdout, _ = _run("--no-file")
        assert code == cash_flow_sync.EXIT_CONFIG_ERROR
        assert _status_line(stdout)["class"] == "config_error"


# ── C17: a Turso failure must not cost a Flex request ──────────────────

class TestWriteFailureIsolation:
    def test_write_failure_exits_fourteen(self, no_network, no_credentials, monkeypatch):
        import db.writer as writer_mod

        failing = _CapturingWriter(fail_on_call=1)
        monkeypatch.setattr(writer_mod, "upsert_cash_flow_rows", failing, raising=False)
        monkeypatch.setattr(cash_flow_sync, "_load_existing_cash_flow_ids", lambda: set())

        code, stdout, _ = _run("--from-file", str(LEGACY_FIXTURE), "--no-file")

        assert code == cash_flow_sync.EXIT_WRITE_ERROR
        assert _status_line(stdout)["class"] == "write_error"

    def test_retry_after_a_write_failure_replays_the_file(
        self, no_network, no_credentials, monkeypatch
    ):
        """The fetch already succeeded. Retrying the WRITE must not spend a
        second SendRequest — that is the amplification path that walked the
        token into the sliding window."""
        import db.writer as writer_mod

        failing = _CapturingWriter(fail_on_call=1)
        monkeypatch.setattr(writer_mod, "upsert_cash_flow_rows", failing, raising=False)
        monkeypatch.setattr(cash_flow_sync, "_load_existing_cash_flow_ids", lambda: set())
        assert _run("--from-file", str(LEGACY_FIXTURE), "--no-file")[0] == (
            cash_flow_sync.EXIT_WRITE_ERROR
        )

        healthy = _CapturingWriter()
        monkeypatch.setattr(writer_mod, "upsert_cash_flow_rows", healthy, raising=False)
        code, _, _ = _run("--from-file", str(LEGACY_FIXTURE), "--no-file")

        assert code == cash_flow_sync.EXIT_OK
        assert len(healthy.written) == 16


class TestLockoutRecordedHonestly:
    """T-100: exit 15 must say whether the token-wide embargo actually landed.
    `record_lockout` raises FlexLockoutNotRecorded when neither the sidecar
    nor the service_health row was written; the status line must not read
    as recorded in that case."""

    LOCKED = (
        "<FlexStatementResponse><Status>Fail</Status>"
        "<ErrorCode>1025</ErrorCode>"
        "<ErrorMessage>Too many failed attempts. Please review your "
        "configuration.</ErrorMessage></FlexStatementResponse>"
    )

    def test_status_line_reports_embargo_recorded_when_a_sink_landed(
        self, credentials, writer
    ):
        with patch.object(cash_flow_sync, "urlopen") as mock_urlopen, \
             patch.object(cash_flow_sync.time, "sleep"), \
             patch("utils.flex_embargo.record_lockout", return_value="2026-08-28T13:58:26Z"):
            mock_urlopen.side_effect = [_xml_response(self.LOCKED)]
            code, stdout, _ = _run("--no-file")

        assert code == cash_flow_sync.EXIT_FLEX_LOCKOUT
        status = _status_line(stdout)
        assert status["class"] == "lockout"
        assert status["embargo_recorded"] is True

    def test_status_line_reports_embargo_not_recorded_when_no_sink_landed(
        self, credentials, writer
    ):
        from utils.flex_embargo import FlexLockoutNotRecorded

        with patch.object(cash_flow_sync, "urlopen") as mock_urlopen, \
             patch.object(cash_flow_sync.time, "sleep"), \
             patch("utils.flex_embargo.record_lockout",
                   side_effect=FlexLockoutNotRecorded("2026-08-28T13:58:26Z")):
            mock_urlopen.side_effect = [_xml_response(self.LOCKED)]
            code, stdout, stderr = _run("--no-file")

        assert code == cash_flow_sync.EXIT_FLEX_LOCKOUT
        status = _status_line(stdout)
        assert status["class"] == "lockout"
        assert status["embargo_recorded"] is False
        assert "not recorded" in stderr.lower()
