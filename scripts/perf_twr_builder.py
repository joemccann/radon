"""TWR performance builder — ingest, integrity gates, payload assembly, persistence.

Every formula lives in `scripts/lib/twr_math.py`; every threshold in
`scripts/lib/twr_gates.py`; the one flow classifier in `scripts/lib/flex_flows.py`.
This module resolves configuration, talks to Flex / Yahoo / disk / Turso, applies
the data-integrity gates, and assembles the v2 payload. It contains no arithmetic
of its own.

The three gates a payload can never pass with status "ok":

1. External flows must have been OBSERVED (`ok` or `empty_verified`). A failed
   fetch publishes `degraded` with `twr: null`, never an empty flow map.
2. NAV must be a live Flex fetch inside the staleness budget. A disk or Turso
   fallback is at best `stale`; an old one is `degraded`.
3. Benchmark statistics are produced by one function from one aligned sample,
   or not at all. There is no partially populated benchmark block.
"""

from __future__ import annotations

import json as _json
import os as _os
import sys as _sys
import time as _time
import xml.etree.ElementTree as _ET
from dataclasses import dataclass as _dataclass
from datetime import date as _date
from datetime import datetime as _dt
from datetime import timedelta as _timedelta
from datetime import timezone as _tz
from pathlib import Path as _Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

_PROJECT_ROOT = _Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_PROJECT_ROOT))

from scripts.lib import twr_gates  # noqa: E402
from scripts.lib.flex_flows import parse_flows  # noqa: E402
from scripts.utils.market_calendar import last_completed_session_date  # noqa: E402
from scripts.utils.market_calendar import load_holidays  # noqa: E402
from scripts.lib.twr_math import (  # noqa: E402
    DuplicateNavDate,
    FlowSet,
    FlowsStatus,
    GatedValue,
    NavObservation,
    NonFiniteInput,
    SubperiodChain,
    UnknownFlowType,
    align_series,
    annualize_return,
    build_benchmark_block,
    build_cashflow_vector,
    build_subperiods,
    conditional_var,
    consolidate_accounts,
    daily_risk_free,
    downside_deviation,
    drawdown_path,
    drawdowns,
    historical_var,
    modified_dietz,
    place_flows,
    return_distribution,
    returns_from_closes,
    sharpe_ratio,
    sortino_ratio,
    volatility,
    xirr,
)

try:
    from clients.fred_client import get_risk_free_rate  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    try:
        from scripts.clients.fred_client import get_risk_free_rate  # type: ignore
    except Exception:  # pragma: no cover
        def get_risk_free_rate(*a, **kw):  # type: ignore
            return 0.0, "Rf unavailable — fred_client not found"

SCHEMA_VERSION = 2
CURVE_TYPE = "twr_daily_bod"
RETURN_BASIS = "time_weighted"
DAY_COUNT = "act/365"
BENCHMARK_BASIS = "price_return"
DEFAULT_BENCHMARK_SYMBOL = "SPY"
DEFAULT_ACCOUNT_ID = "ALL"


@_dataclass(frozen=True)
class FlexDocument:
    """One attempted Flex statement, kept so a query id is requested only once.

    A failed attempt is still an attempt. `xml` is None and `error` carries the
    upstream message, so a later leg can tell "this id already failed" apart
    from "this id was never tried" and decline to re-request it.
    """

    query_id: str
    xml: Optional[str] = None
    error: Optional[str] = None


@_dataclass(frozen=True)
class NavResolution:
    """A resolved NAV series and everything the payload needs to explain it."""

    by_date: Dict[str, float]
    source: str
    account_gaps: Tuple[str, ...] = ()
    document: Optional[FlexDocument] = None

_DATA_DIR = _PROJECT_ROOT / "data"
_PERF_PATH = _DATA_DIR / "performance.json"
_NAV_CACHE_PATH = _DATA_DIR / "nav_history_ib.json"
_IB_NAV_CACHE_DIR = _DATA_DIR / "ib_nav_cache"

_NAV_DISK_MAX_AGE_DAYS = 30

_FLEX_SEND = "https://gdcdyn.interactivebrokers.com/Universal/servlet/FlexStatementService.SendRequest"
_FLEX_GET = "https://gdcdyn.interactivebrokers.com/Universal/servlet/FlexStatementService.GetStatement"

_STATUS_ORDER = ("ok", "stale", "degraded", "insufficient_data", "unavailable")
_SEVERITY_FLOOR = {"info": "ok", "warn": "stale", "error": "degraded"}
_NAV_SOURCE_WARNING = {"disk_cache": "NAV_SOURCE_DISK", "turso": "NAV_SOURCE_TURSO"}

_RISK_MIN_N = {
    "volatility": twr_gates.MIN_N_DISPERSION,
    "sharpe_ratio": twr_gates.MIN_N_RATIO,
    "sortino_ratio": twr_gates.MIN_N_RATIO,
    "downside_deviation": twr_gates.MIN_N_DISPERSION,
    "max_drawdown": twr_gates.MIN_N_DISPERSION,
    "current_drawdown": twr_gates.MIN_N_DISPERSION,
    "var_95": twr_gates.MIN_N_DISPERSION,
    "cvar_95": twr_gates.MIN_N_DISPERSION,
}

_DISTRIBUTION_KEYS = (
    "hit_rate",
    "best_day",
    "worst_day",
    "average_up_day",
    "average_down_day",
    "win_loss_ratio",
    "positive_days",
    "negative_days",
    "flat_days",
)


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------


def _optional_float(value: Any) -> Optional[float]:
    """A Flex numeric attribute, or None when it is absent or unreadable.

    There is deliberately no default. A missing NAV total must DROP its row:
    manufacturing a 0.0 here is what chained a blank attribute as a -100%
    session and published it under status "ok".
    """
    if value is None or (isinstance(value, str) and value.strip().lower() in ("", "nan")):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _now_iso() -> str:
    return _dt.now(_tz.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _normalize_date(raw: Any) -> Optional[str]:
    text = str(raw or "").strip()
    if not text:
        return None
    head = text.split(";")[0].split(" ")[0].split("T")[0]
    if len(head) == 8 and head.isdigit():
        return f"{head[:4]}-{head[4:6]}-{head[6:]}"
    if len(head) == 10 and head[4] == "-" and head[7] == "-":
        return head
    return None


def _warning(code: str, severity: str, message: str, **context: Any) -> Dict[str, Any]:
    return {"code": code, "severity": severity, "message": message, "context": context}


def _calendar_days(dates: Sequence[str]) -> int:
    if len(dates) < 2:
        return 0
    return (_date.fromisoformat(dates[-1]) - _date.fromisoformat(dates[0])).days


def _resolve_status(base: str, warnings: Sequence[Mapping[str, Any]]) -> str:
    floors = [base] + [_SEVERITY_FLOOR[w["severity"]] for w in warnings]
    return max(floors, key=_STATUS_ORDER.index)


def last_completed_session(now: Optional[_dt] = None) -> _date:
    """The most recent session whose 16:00 ET close has passed.

    The one boundary `sessions_behind` counts up to, mirrored in TypeScript.
    A build at 20:45 ET counts the session that just closed; a build at 09:00
    ET does not count the one now in progress; a weekend or a holiday resolves
    back to the previous session.
    """
    return _date.fromisoformat(last_completed_session_date(now))


def _is_session(day: _date, holidays: set[str]) -> bool:
    return day.weekday() < 5 and day.isoformat() not in holidays


def sessions_behind(nav_as_of: Optional[str], through: Optional[_date] = None) -> int:
    """Trading sessions strictly after `nav_as_of`, up to and including `through`.

    `through` is a COMPLETED session, defaulting to `last_completed_session()`.
    Both ends are pinned because the two off-by-one readings of "behind" have
    each shipped: counting the in-progress session turns a fresh NAV amber at
    any hour, and stopping short of the last completed session keeps the amber
    banner off a NAV that has aged out of the budget.
    """
    if not nav_as_of:
        return 0
    start = _date.fromisoformat(nav_as_of)
    end = through or last_completed_session()
    holidays = {str(h) for year in range(start.year, end.year + 1) for h in load_holidays(year)}
    count = 0
    cursor = start + _timedelta(days=1)
    while cursor <= end:
        if _is_session(cursor, holidays):
            count += 1
        cursor += _timedelta(days=1)
    return count


# ---------------------------------------------------------------------------
# Flex / disk / Turso ingest
# ---------------------------------------------------------------------------


FLEX_READ_TIMEOUT_SECONDS = 120.0
FLEX_POLL_BUDGET_SECONDS = 420.0


def fetch_flex_xml(
    token: str,
    query_id: str,
    *,
    max_polls: int = 60,
    poll_secs: float = 3.0,
    read_timeout: float = FLEX_READ_TIMEOUT_SECONDS,
) -> str:
    """Poll Flex until FlexStatements appears; hard-fail if ReferenceCode is missing.

    Two budgets, both widened after a live failure on 2026-08-16.

    The socket read timeout was 30s. Query 1442520 now carries three sections
    (NAV in Base, Cash Transactions, Transfers), and the Transfers section made
    the statement slow enough that GetStatement blew that timeout outright:
    ``fetch_failed: The read operation timed out``. The builder degraded
    correctly rather than publishing, but the payload could never regenerate.

    The poll budget was 30 x 3s = 90 seconds flat. IBKR statement generation at
    the EOD spike routinely runs into minutes. This is the same wall that made
    `cash_flow_sync` fail 14 times out of 16 -- a caller-side deadline shorter
    than the work it is waiting on -- so the budget matches the 420s that script
    now uses, and the sleep is capped-exponential rather than a fixed 3s.
    """
    from urllib.parse import urlencode
    from urllib.request import urlopen

    try:
        from utils.flex_embargo import raise_if_blocked, record_lockout
    except Exception:  # pragma: no cover — embargo is belt-and-suspenders
        raise_if_blocked = None  # type: ignore[assignment]
        record_lockout = None  # type: ignore[assignment]
    if raise_if_blocked is not None:
        raise_if_blocked()

    def _arm_lockout(code: str) -> None:
        if code == "1025" and record_lockout is not None:
            try:
                record_lockout(code)
            except Exception:
                return

    params = urlencode({"t": token, "q": query_id, "v": "3"})
    resp = urlopen(f"{_FLEX_SEND}?{params}", timeout=read_timeout)  # noqa: S310
    text = resp.read().decode("utf-8")
    root = _ET.fromstring(text)
    ref = root.find(".//ReferenceCode")
    if ref is None or not (ref.text or "").strip():
        err_code = (root.findtext(".//ErrorCode") or "").strip()
        err_msg = root.findtext(".//ErrorMessage") or text[:500]
        _arm_lockout(err_code)
        raise RuntimeError(f"Flex SendRequest failed code={err_code}: {err_msg}")
    ref_code = ref.text.strip()  # type: ignore[union-attr]
    elapsed = 0.0
    sleep_s = float(poll_secs)
    for _ in range(max_polls):
        if elapsed + sleep_s > FLEX_POLL_BUDGET_SECONDS:
            break
        _time.sleep(sleep_s)
        elapsed += sleep_s
        sleep_s = min(sleep_s * 2, 15.0)
        params2 = urlencode({"t": token, "q": ref_code, "v": "3"})
        resp2 = urlopen(f"{_FLEX_GET}?{params2}", timeout=read_timeout)  # noqa: S310
        xml_text = resp2.read().decode("utf-8")
        # Do not match FlexStatementResponse: that wrapper is the ERROR
        # envelope, and "<FlexStatement" is a prefix of it. Treating 1025
        # XML as a ready statement is how TWR kept "succeeding" on a lockout.
        if "<FlexStatements" in xml_text:
            return xml_text
        try:
            poll_root = _ET.fromstring(xml_text)
        except _ET.ParseError:
            continue
        err_code = (poll_root.findtext(".//ErrorCode") or "").strip()
        # 1019 is ordinary not-ready. 1018/1025 are token-level; polling
        # them for the rest of the budget is how a lockout stays locked.
        if err_code not in ("1018", "1025"):
            continue
        err_msg = (poll_root.findtext(".//ErrorMessage") or xml_text[:500]).strip()
        _arm_lockout(err_code)
        raise RuntimeError(f"Flex GetStatement failed code={err_code}: {err_msg}")
    raise RuntimeError(
        f"Flex GetStatement not ready within {FLEX_POLL_BUDGET_SECONDS:.0f}s ref={ref_code}"
    )


def _account_scopes(root: _ET.Element) -> List[Tuple[str, _ET.Element]]:
    """(account_id, subtree) per FlexStatement — IBKR emits one per account."""
    statements = root.findall(".//FlexStatement")
    if not statements:
        return [(DEFAULT_ACCOUNT_ID, root)]
    return [(stmt.get("accountId") or DEFAULT_ACCOUNT_ID, stmt) for stmt in statements]


def parse_nav_by_account(xml_text: str) -> Dict[str, Dict[str, float]]:
    """Parse EquitySummaryByReportDateInBase -> {account: {date: total_net_liq}}.

    Keeping the accounts apart is what lets consolidation drop a date one
    account is missing. Flattening here is how a two-account statement summed
    every deposit while keeping only the last statement's NAV.
    """
    root = _ET.fromstring(xml_text)
    by_account: Dict[str, Dict[str, float]] = {}
    for scope_account, scope in _account_scopes(root):
        for node in scope.findall(".//EquitySummaryByReportDateInBase"):
            report_date = _normalize_date(node.get("reportDate") or node.get("report_date"))
            total = _optional_float(node.get("total"))
            if not report_date or total is None:
                continue
            account = node.get("accountId") or scope_account
            by_account.setdefault(account, {})[report_date] = total
    return by_account


def consolidate_nav(
    per_account: Mapping[str, Mapping[str, float]]
) -> Tuple[Dict[str, float], List[str]]:
    """Sum every account's NAV per date. Dates an account is missing are dropped."""
    if not per_account:
        return {}, []
    observations, _flows, gap_dates = consolidate_accounts(per_account)
    return {o.date: o.nav for o in observations}, list(gap_dates)


def parse_nav_entries(xml_text: str) -> Dict[str, float]:
    """Consolidated NAV series from one Flex statement."""
    nav, _gaps = consolidate_nav(parse_nav_by_account(xml_text))
    return nav


def _nav_rows_to_map(rows: Any) -> Dict[str, float]:
    out: Dict[str, float] = {}
    if isinstance(rows, list):
        for row in rows:
            report_date = _normalize_date(row.get("date") or row.get("reportDate"))
            total = _optional_float(row.get("total", row.get("net_liq")))
            if report_date and total is not None:
                out[report_date] = total
    elif isinstance(rows, dict):
        for key, value in rows.items():
            report_date = _normalize_date(key)
            total = _optional_float(value)
            if report_date and total is not None:
                out[report_date] = total
    return out


def _is_within_disk_budget(nav: Mapping[str, float]) -> bool:
    if not nav:
        return False
    newest = _date.fromisoformat(max(nav))
    return (_date.today() - newest).days <= _NAV_DISK_MAX_AGE_DAYS


def load_nav_from_disk() -> Optional[Dict[str, float]]:
    """Disk NAV cache, refused outright once it is older than the age budget.

    The live defect served a 2026-03-20 series on 2026-08-15 under status "ok".
    A cache this old is not a fallback, it is a different account state.
    """
    for path in _disk_nav_candidates():
        try:
            nav = _nav_rows_to_map(_json.loads(path.read_text()))
        except (_json.JSONDecodeError, OSError, AttributeError):
            continue
        if len(nav) < 2:
            continue
        if not _is_within_disk_budget(nav):
            print(f"[perf_twr] Ignoring stale disk NAV cache {path} (newest {max(nav)})", file=_sys.stderr)
            continue
        return nav
    return None


def _disk_nav_candidates() -> List[_Path]:
    candidates: List[_Path] = []
    if _NAV_CACHE_PATH.exists():
        candidates.append(_NAV_CACHE_PATH)
    if _IB_NAV_CACHE_DIR.exists() and _IB_NAV_CACHE_DIR.is_dir():
        try:
            cached = sorted(
                _IB_NAV_CACHE_DIR.glob("*.json"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            candidates.extend(cached[:3])
        except OSError:
            pass
    return candidates


def load_nav_from_turso() -> Optional[Dict[str, float]]:
    if not _os.environ.get("TURSO_DB_URL") or not _os.environ.get("TURSO_AUTH_TOKEN"):
        return None
    try:
        try:
            from db.client import get_db  # type: ignore
        except ImportError:
            from scripts.db.client import get_db  # type: ignore

        db = get_db()
        for query, date_key, value_key in (
            ("SELECT date, net_liq FROM nav_history ORDER BY date ASC", "date", "net_liq"),
            (
                "SELECT report_date, total_net_liq FROM nav_snapshots ORDER BY report_date ASC",
                "report_date",
                "total_net_liq",
            ),
        ):
            try:
                rows = db.execute(query).fetchall()
            except Exception:  # noqa: BLE001
                continue
            out: Dict[str, float] = {}
            for row in rows:
                if isinstance(row, dict):
                    report_date = _normalize_date(row.get(date_key))
                    value = _optional_float(row.get(value_key))
                else:
                    report_date = _normalize_date(row[0])
                    value = _optional_float(row[1])
                if report_date and value is not None:
                    out[report_date] = value
            if len(out) >= 2:
                return out
    except Exception:  # noqa: BLE001
        pass
    return None


def _fetch_nav_document() -> Optional[FlexDocument]:
    token = _os.environ.get("IB_FLEX_TOKEN")
    query_id = _os.environ.get("IB_FLEX_NAV_QUERY_ID")
    if not token or not query_id:
        return None
    try:
        return FlexDocument(query_id=query_id, xml=fetch_flex_xml(token, query_id))
    except Exception as exc:  # noqa: BLE001
        print(f"[perf_twr] Flex NAV fetch failed: {exc}", file=_sys.stderr)
        return FlexDocument(query_id=query_id, error=str(exc))


def get_nav_snapshots() -> NavResolution:
    """Resolve NAV: live Flex -> disk cache -> Turso.

    The fetched statement travels with the result so the flows query can be
    parsed out of the document already in memory. IBKR throttles repeat
    requests for one query id into an embargo, and with
    `IB_FLEX_FLOWS_QUERY_ID` unset both queries resolve to the same id.
    """
    document = _fetch_nav_document()
    entries, gap_dates = consolidate_nav(
        parse_nav_by_account(document.xml)
        if document is not None and document.xml is not None
        else {}
    )
    if entries:
        _cache_nav_to_disk(entries)
        return NavResolution(entries, "flex_live", tuple(gap_dates), document)

    disk = load_nav_from_disk()
    if disk:
        return NavResolution(disk, "disk_cache", (), document)
    turso = load_nav_from_turso()
    if turso:
        return NavResolution(turso, "turso", (), document)
    return NavResolution({}, "none", (), document)


def _cache_nav_to_disk(entries: Mapping[str, float]) -> None:
    try:
        _NAV_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        rows = [{"date": k, "total": v} for k, v in sorted(entries.items())]
        _NAV_CACHE_PATH.write_text(_json.dumps(rows, indent=2))
    except OSError:
        pass


def _query_turso(sql: str) -> Optional[List[Any]]:
    """Rows for one read-only query, or None if Turso is unreachable/unset."""
    if not _os.environ.get("TURSO_DB_URL") or not _os.environ.get("TURSO_AUTH_TOKEN"):
        return None
    try:
        try:
            from db.client import get_db  # type: ignore
        except ImportError:
            from scripts.db.client import get_db  # type: ignore

        return get_db().execute(sql).fetchall()
    except Exception:  # noqa: BLE001
        return None


def _row_values(row: Any, *keys: str) -> Tuple[Any, ...]:
    if isinstance(row, dict):
        return tuple(row.get(k) for k in keys)
    return tuple(row[i] for i in range(len(keys)))


def load_flows_from_turso() -> Optional[Dict[str, float]]:
    """Last-known-good external flows, netted per report date.

    The symmetric counterpart to `load_nav_from_turso`. Returns None when
    nothing is mirrored yet; an empty result is NOT a verified zero.
    """
    rows = _query_turso("SELECT report_date, amount FROM external_flows ORDER BY report_date ASC")
    if rows is None:
        return None

    out: Dict[str, float] = {}
    for row in rows:
        report_date, amount = _row_values(row, "report_date", "amount")
        normalized = _normalize_date(report_date)
        value = _optional_float(amount)
        if normalized and value is not None:
            # One date can carry several flow_type rows; the subperiod maths
            # only ever needs their net.
            out[normalized] = out.get(normalized, 0.0) + value
    return out or None


def load_flows_coverage_through() -> Optional[str]:
    """The last session a good run verified flows for.

    `external_flows` only stores dates that HAD a flow, so its own MAX says
    nothing about coverage. `twr_subperiods` carries a row per session with an
    explicit zero, which makes its MAX the honest coverage marker.
    """
    rows = _query_turso("SELECT MAX(report_date) AS report_date FROM twr_subperiods")
    if not rows:
        return None
    return _normalize_date(_row_values(rows[0], "report_date")[0])


def bound_observations_to_coverage(
    observations: Sequence[NavObservation], covered_through: Optional[str]
) -> Sequence[NavObservation]:
    """Drop NAV past the last session flows were actually verified for.

    Chaining a session the mirror never saw asserts "no external flow that day"
    on no evidence — the invented zero that inflates TWR.
    """
    if not covered_through:
        return observations
    return [o for o in observations if o.date <= covered_through]


def _flows_query_id() -> Optional[str]:
    return _os.environ.get("IB_FLEX_FLOWS_QUERY_ID") or _os.environ.get("IB_FLEX_NAV_QUERY_ID")


def _has_transfers_section(xml_text: str) -> bool:
    try:
        return _ET.fromstring(xml_text).find(".//Transfers") is not None
    except _ET.ParseError:
        return False


def _transfers_section_warnings(xml_text: str, query_id: str) -> List[Dict[str, Any]]:
    """A statement with no Transfers section cannot see a securities transfer.

    An incoming ACATS has no CashTransaction row at all, so this configuration
    reports an arriving position as investment gain. It is an operator fix, not
    a code fix, so the warning names the exact query and fields to add.
    """
    if _has_transfers_section(xml_text):
        return []
    return [
        _warning(
            "FLOWS_TRANSFERS_SECTION_ABSENT",
            "error",
            f"Flex query {query_id} has no Transfers section, so securities transfers "
            "(ACATS, FOP) are invisible and an arriving position reads as investment "
            "gain. In IB Account Management > Performance & Reports > Flex Queries, "
            f"edit query {query_id} and add the Transfers section with the fields "
            "type, direction, assetCategory, reportDate, positionAmountInBase and "
            "cashTransfer, then re-run this build.",
            query_id=query_id,
        )
    ]


def _flows_after_fetch_failure(reason: str) -> Tuple[FlowSet, List[Dict[str, Any]]]:
    """Serve last-known-good flows rather than blanking a page that was correct.

    Flows for closed sessions are settled facts; they do not change because
    IBKR's statement generator is unavailable right now. Falling back to the
    mirror a previous good run wrote degrades the page's provenance instead of
    its numbers, exactly as NAV already degrades through disk_cache -> turso.

    Nothing mirrored stays FAILED: absence of evidence is not a verified zero,
    and an invented empty flow set is what produced the +951% TWR.
    """
    mirrored = load_flows_from_turso()
    if not mirrored:
        return FlowSet.failed(f"fetch_failed: {reason}"), []
    print(
        f"[perf_twr] Serving {len(mirrored)} mirrored flows from Turso after Flex failure",
        file=_sys.stderr,
    )
    return FlowSet(status=FlowsStatus.OK, by_date=mirrored, source="turso"), []


def resolve_flows(document: Optional[FlexDocument] = None) -> Tuple[FlowSet, List[Dict[str, Any]]]:
    """Resolve external flows plus any coverage warnings about the statement.

    The NAV statement is reused when the flows query id matches it, so one
    query id is never requested twice in a run — and a query id that already
    FAILED this run is not requested again either. Retrying it cannot succeed
    seconds later on the same token, and the repeat is what escalates a
    transient 1001 into a 1025 "too many failed attempts" lockout.
    """
    token = _os.environ.get("IB_FLEX_TOKEN")
    query_id = _flows_query_id()
    if not token or not query_id:
        return FlowSet.failed("flex_not_configured"), []

    already_attempted = document is not None and document.query_id == query_id
    if already_attempted and document.xml is None:
        reason = document.error or "nav_fetch_failed"
        print(
            f"[perf_twr] Flex flows fetch skipped: query {query_id} already failed "
            f"this run ({reason})",
            file=_sys.stderr,
        )
        return _flows_after_fetch_failure(reason)

    try:
        statement = document.xml if already_attempted else fetch_flex_xml(token, query_id)
    except Exception as exc:  # noqa: BLE001
        print(f"[perf_twr] Flex flows fetch failed: {exc}", file=_sys.stderr)
        return _flows_after_fetch_failure(str(exc))

    coverage = _transfers_section_warnings(statement, query_id)
    try:
        return parse_flows(statement), coverage
    except UnknownFlowType as exc:
        print(f"[perf_twr] Unknown flow type: {exc}", file=_sys.stderr)
        return FlowSet.failed(f"unknown_flow_type: {exc}"), coverage
    except Exception as exc:  # noqa: BLE001
        print(f"[perf_twr] Flex flows parse failed: {exc}", file=_sys.stderr)
        return FlowSet.failed(f"parse_failed: {exc}"), coverage


def get_external_flows_for_nav(document: Optional[FlexDocument] = None) -> FlowSet:
    """Fetch and classify external flows. A failure is FAILED, never empty."""
    flows, _coverage = resolve_flows(document)
    return flows


def load_benchmark_closes(start: str, end: str, symbol: str = DEFAULT_BENCHMARK_SYMBOL) -> Dict[str, float]:
    """Single-source benchmark closes via Yahoo. Empty dict on any failure."""
    try:
        from urllib.request import Request, urlopen

        start_ts = int(_dt.strptime(start, "%Y-%m-%d").replace(tzinfo=_tz.utc).timestamp())
        end_ts = int(_dt.strptime(end, "%Y-%m-%d").replace(tzinfo=_tz.utc).timestamp()) + 86400
        url = (
            f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
            f"?period1={start_ts}&period2={end_ts}&interval=1d&includePrePost=false"
        )
        with urlopen(Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=15) as resp:  # noqa: S310
            data = _json.loads(resp.read().decode("utf-8"))
        result = (data.get("chart") or {}).get("result") or []
        if not result:
            return {}
        timestamps = result[0].get("timestamp") or []
        closes = (result[0].get("indicators") or {}).get("quote", [{}])[0].get("close") or []
        out: Dict[str, float] = {}
        for ts, close in zip(timestamps, closes):
            if close is None:
                continue
            day = _dt.fromtimestamp(int(ts), tz=_tz.utc).strftime("%Y-%m-%d")
            if start <= day <= end:
                out[day] = float(close)
        return out
    except Exception as exc:  # noqa: BLE001
        print(f"[perf_twr] {symbol} fetch failed: {exc}", file=_sys.stderr)
        return {}


# ---------------------------------------------------------------------------
# payload assembly
# ---------------------------------------------------------------------------


def _methodology(
    risk_free_rate: float, risk_free_source: str, inferred: Sequence[Mapping[str, Any]] = ()
) -> Dict[str, Any]:
    return {
        "curve_type": CURVE_TYPE,
        "return_basis": RETURN_BASIS,
        "flow_convention": twr_gates.FLOW_CONVENTION,
        "day_count": DAY_COUNT,
        "vol_scaling_days": twr_gates.TRADING_DAYS,
        "sortino_target": twr_gates.SORTINO_TARGET,
        "risk_free_rate": float(risk_free_rate),
        "risk_free_source": risk_free_source,
        "benchmark_basis": BENCHMARK_BASIS,
        "inferred_flows": list(inferred),
    }


def _suppressed_metrics(reason: str, n: int) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    risk = {
        name: GatedValue(None, n, min_n, reason).as_dict()
        for name, min_n in _RISK_MIN_N.items()
    }
    distribution = {
        name: GatedValue(None, n, twr_gates.MIN_N_DISPERSION, reason).as_dict()
        for name in _DISTRIBUTION_KEYS
    }
    return risk, distribution


def _freshness_warnings(nav_source: str, behind: int, nav_as_of: Optional[str]) -> List[Dict[str, Any]]:
    stale = behind > twr_gates.NAV_STALENESS_BUDGET_SESSIONS
    out: List[Dict[str, Any]] = []
    source_code = _NAV_SOURCE_WARNING.get(nav_source)
    if source_code:
        out.append(
            _warning(
                source_code,
                # Provenance, not freshness. `warn` floors the payload to
                # "stale" (_SEVERITY_FLOOR), and the render layer gates the TWR
                # on "stale" exactly as on "degraded" -- so serving a perfectly
                # current NAV from a cache blanked the whole page. Age is
                # already policed by _NAV_DISK_MAX_AGE_DAYS here and by the
                # read layer re-deriving sessionsBehind from nav_as_of.
                "error" if stale else "info",
                f"NAV came from the {nav_source} fallback, not a live Flex fetch.",
                nav_source=nav_source,
                nav_as_of=nav_as_of,
            )
        )
    if stale:
        out.append(
            _warning(
                "NAV_STALE",
                "error" if source_code else "warn",
                f"NAV is {behind} sessions behind the last completed session.",
                nav_as_of=nav_as_of,
                sessions_behind=behind,
            )
        )
    return out


def _placement_warnings(placement: Any) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for flow_date, applied in placement.shifted:
        out.append(
            _warning(
                "FLOW_DATE_SHIFTED",
                "info",
                f"Flow dated {flow_date} has no NAV row; applied to {applied}.",
                flow_date=flow_date,
                applied_date=applied,
            )
        )
    for flow_date in placement.after_period_end:
        out.append(
            _warning(
                "FLOW_AFTER_PERIOD_END",
                "info",
                f"Flow dated {flow_date} is after the last NAV observation; dropped.",
                flow_date=flow_date,
            )
        )
    return out


def _account_gap_warnings(gap_dates: Sequence[str]) -> List[Dict[str, Any]]:
    """One warning per date an account of the consolidated group is missing.

    The date is dropped rather than summed short: a group total missing one
    account's NAV is a fabricated drawdown.
    """
    return [
        _warning(
            "NAV_ACCOUNT_GAP",
            "warn",
            f"{gap_date} is missing from at least one account of the consolidated "
            "group; the date is dropped rather than summed short.",
            date=gap_date,
        )
        for gap_date in gap_dates
    ]


def _is_extreme(subperiod: Any) -> bool:
    """A chained move past the suspect threshold."""
    return (
        subperiod.ret is not None
        and abs(subperiod.ret) > twr_gates.SUSPECT_RETURN_THRESHOLD
    )


def _extreme_message(subperiod: Any) -> str:
    """Name what the session actually has on file, never a flow of zero.

    "returned -100.00% with a recorded +0.00 flow" contradicts itself: a zero
    flow is the absence of a record, not a record.
    """
    if subperiod.flow == 0.0:
        return (
            f"{subperiod.date} returned {subperiod.ret:+.2%} with no recorded "
            "external flow; chained, but the move is not a market outcome."
        )
    return (
        f"{subperiod.date} returned {subperiod.ret:+.2%} against a recorded "
        f"{subperiod.flow:+,.2f} flow; chained, but check the flow is complete."
    )


def _subperiod_warnings(chain: SubperiodChain) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for subperiod in chain.subperiods:
        if "flow_dominant" in subperiod.flags:
            out.append(
                _warning(
                    "FLOW_DOMINANT",
                    "info",
                    f"{subperiod.date} is dominated by a {subperiod.flow:+,.2f} external "
                    "flow; the return is measured on the pre-flow base.",
                    date=subperiod.date,
                    flow=subperiod.flow,
                    denominator=subperiod.denominator,
                )
            )
        if _is_extreme(subperiod):
            out.append(
                _warning(
                    "SUBPERIOD_EXTREME",
                    "error",
                    _extreme_message(subperiod),
                    date=subperiod.date,
                    ret=subperiod.ret,
                    flow=subperiod.flow,
                )
            )
        if "nav_gap" in subperiod.flags:
            out.append(
                _warning(
                    "NAV_GAP",
                    "info",
                    f"{subperiod.gap_days} calendar days with no NAV row before {subperiod.date}.",
                    date=subperiod.date,
                    gap_days=subperiod.gap_days,
                )
            )
        if subperiod.skip_reason == "suspect_no_flow":
            # The residual is what is unexplained, not the raw NAV move. Saying
            # "no recorded external flow" is false whenever a flow WAS recorded
            # and merely failed to account for the whole move -- which is the
            # normal case for an ACATS priced at transfer time rather than at
            # the close. Report both numbers so the operator can tell a missing
            # transfer apart from a valuation difference.
            moved = subperiod.end_nav - subperiod.begin_nav
            residual = moved - subperiod.flow
            explained = (
                "with no recorded external flow"
                if subperiod.flow == 0.0
                else f"against a recorded {subperiod.flow:+,.2f} external flow, "
                f"leaving {residual:+,.2f} unexplained"
            )
            out.append(
                _warning(
                    "SUBPERIOD_SUSPECT",
                    "error",
                    f"{subperiod.date} moved {moved:+,.2f} {explained}; "
                    "excluded from the chain.",
                    date=subperiod.date,
                    begin_nav=subperiod.begin_nav,
                    end_nav=subperiod.end_nav,
                    flow=subperiod.flow,
                    residual=residual,
                )
            )
        elif subperiod.skip_reason:
            out.append(
                _warning(
                    "SUBPERIOD_SKIPPED",
                    "info",
                    f"{subperiod.date} skipped: {subperiod.skip_reason}.",
                    date=subperiod.date,
                    skip_reason=subperiod.skip_reason,
                )
            )
    return out


def _inferred_candidates(chain: SubperiodChain) -> List[Dict[str, Any]]:
    return [
        {"date": sp.date, "amount": sp.end_nav - sp.begin_nav}
        for sp in chain.subperiods
        if sp.skip_reason == "suspect_no_flow"
    ]


def _series_points(
    observations: Sequence[NavObservation],
    chain: SubperiodChain,
    benchmark_closes: Optional[Mapping[str, float]],
    benchmark_returns: Optional[Mapping[str, float]],
) -> List[Dict[str, Any]]:
    by_date = {sp.date: sp for sp in chain.subperiods}
    points: List[Dict[str, Any]] = []
    cum_return = 0.0
    twr_index = 100.0

    for position, observation in enumerate(observations):
        subperiod = by_date.get(observation.date) if position else None
        daily_return = None
        flow = 0.0
        skipped = False
        if subperiod is not None:
            flow = subperiod.flow
            if subperiod.ret is None:
                skipped = True
            else:
                daily_return = subperiod.ret
                cum_return = subperiod.cum_ret or 0.0
                twr_index = 100.0 * (1.0 + cum_return)
        point: Dict[str, Any] = {
            "date": observation.date,
            "nav": observation.nav,
            "twr_index": twr_index,
            "daily_return": daily_return,
            "cum_return": cum_return,
            "flow": flow,
            "skipped": skipped,
        }
        if benchmark_closes is not None:
            point["benchmark_close"] = benchmark_closes.get(observation.date)
            point["benchmark_return"] = (benchmark_returns or {}).get(observation.date)
        points.append(point)

    for point, drawdown in zip(points, drawdown_path([p["twr_index"] for p in points])):
        point["drawdown"] = drawdown
    return points


def _subperiod_rows(chain: SubperiodChain) -> List[Dict[str, Any]]:
    return [
        {
            "date": sp.date,
            "b": sp.begin_nav,
            "e": sp.end_nav,
            "c": sp.flow,
            "denominator": sp.denominator,
            "r": sp.ret,
            "cum_r": sp.cum_ret,
            "gap_days": sp.gap_days,
            "flags": list(sp.flags),
            "skip_reason": sp.skip_reason,
        }
        for sp in chain.subperiods
    ]


def _shell(
    *,
    status: str,
    account_id: str,
    methodology: Mapping[str, Any],
    nav_source: str,
    nav_as_of: Optional[str],
    nav_sessions_behind: int,
    flows: FlowSet,
    period_start: Optional[str],
    period_end: Optional[str],
    calendar_days: int,
    counts: Mapping[str, int],
    warnings: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "generated_at": _now_iso(),
        "account_id": account_id,
        "methodology": dict(methodology),
        "nav_source": nav_source,
        "nav_as_of": nav_as_of,
        "nav_sessions_behind": nav_sessions_behind,
        "flows_status": flows.status.value,
        "flows_source": flows.source,
        "period_start": period_start,
        "period_end": period_end,
        "calendar_days": calendar_days,
        "counts": dict(counts),
        "twr": None,
        "mwr": {},
        "risk": {},
        "drawdown_detail": {},
        "distribution": {},
        "benchmark": None,
        "equity": {},
        "series": [],
        "subperiods": [],
        "warnings": list(warnings),
    }


def _suppressed_payload(
    *,
    status: str,
    reason: str,
    observations: Sequence[NavObservation],
    flows: FlowSet,
    warnings: Sequence[Mapping[str, Any]],
    methodology: Mapping[str, Any],
    account_id: str,
    nav_source: str,
    nav_sessions_behind: int,
) -> Dict[str, Any]:
    by_date = {o.date: float(o.nav) for o in observations}
    dates = sorted(by_date)
    n_subperiods = max(len(dates) - 1, 0)
    risk, distribution = _suppressed_metrics(reason, n_subperiods)
    # A GatedValue's `n` is the sample the NAV series SUPPORTS, so a card never
    # blames its sample size for a suppression that has nothing to do with N.
    # `counts.n_returns` is the opposite question — how many returns this branch
    # actually chained — and on a suppressed payload that is zero. Reporting the
    # supported sample there is what rendered "N=60" beside an empty series.
    gated = GatedValue(None, n_subperiods, twr_gates.MIN_N_MWR, reason).as_dict()

    payload = _shell(
        status=status,
        account_id=account_id,
        methodology=methodology,
        nav_source=nav_source,
        nav_as_of=dates[-1] if dates else None,
        nav_sessions_behind=nav_sessions_behind,
        flows=flows,
        period_start=dates[0] if dates else None,
        period_end=dates[-1] if dates else None,
        calendar_days=_calendar_days(dates),
        counts={
            "n_nav_observations": len(dates),
            "n_subperiods": n_subperiods,
            "n_returns": 0,
            "n_skipped": 0,
            "n_suspect": 0,
        },
        warnings=warnings,
    )
    payload["mwr"] = {
        "period_return": gated,
        "annualized": dict(gated),
        "multiple_sign_changes": False,
    }
    payload["risk"] = risk
    payload["distribution"] = distribution
    payload["drawdown_detail"] = {
        "trough_date": None,
        "peak_date": None,
        "trough_days": None,
        "recovery_days": None,
        "ongoing": False,
    }
    # The endpoints of the window are observed facts and stay published. What
    # the flows would have told us is null with a reason, never a fabricated 0.
    payload["equity"] = {
        "starting": by_date[dates[0]] if dates else None,
        "ending": by_date[dates[-1]] if dates else None,
        "net_external_flows": None,
        "investment_pnl": None,
    }
    return payload


def build_payload(
    nav: Sequence[NavObservation],
    flows: FlowSet,
    *,
    benchmark: Optional[Mapping[str, float]] = None,
    benchmark_symbol: str = DEFAULT_BENCHMARK_SYMBOL,
    risk_free_rate: float = 0.0,
    risk_free_source: str = "fallback_zero",
    nav_source: str = "flex_live",
    nav_sessions_behind: int = 0,
    account_id: str = DEFAULT_ACCOUNT_ID,
    allow_inferred_flows: bool = False,
    ingest_warnings: Sequence[Mapping[str, Any]] = (),
) -> Dict[str, Any]:
    """Assemble the v2 performance payload from a resolved NAV series and FlowSet.

    `ingest_warnings` carry what only the resolver could observe (a consolidated
    account gap, a Flex query with no Transfers section) into the same warnings
    array as everything else, so they move the status floor identically.
    """
    observations = list(nav or ())
    methodology = _methodology(risk_free_rate, risk_free_source)
    ingest = list(ingest_warnings)

    if not observations:
        return _suppressed_payload(
            status="unavailable",
            reason="not_computed",
            observations=(),
            flows=flows,
            warnings=ingest
            + [
                _warning(
                    "NAV_UNAVAILABLE",
                    "error",
                    "No NAV series available (Flex, disk and Turso all empty).",
                )
            ],
            methodology=methodology,
            account_id=account_id,
            nav_source=nav_source,
            nav_sessions_behind=nav_sessions_behind,
        )

    # Ingest warnings lead: they name an operator action, and the banner shows
    # the first error. A generic "flows unavailable" above "your Flex query is
    # missing its Transfers section" buries the only actionable line.
    freshness = _freshness_warnings(
        nav_source, nav_sessions_behind, max(o.date for o in observations)
    )

    if not flows.is_usable:
        return _suppressed_payload(
            status="degraded",
            reason="no_flow_data",
            observations=observations,
            flows=flows,
            warnings=ingest
            + [
                _warning(
                    "FLOWS_FETCH_FAILED",
                    "error",
                    f"External flows could not be fetched ({flows.reason}); TWR is suppressed.",
                    reason=flows.reason,
                )
            ]
            + freshness,
            methodology=methodology,
            account_id=account_id,
            nav_source=nav_source,
            nav_sessions_behind=nav_sessions_behind,
        )

    try:
        chain = build_subperiods(observations, flows.by_date)
    except DuplicateNavDate as exc:
        return _suppressed_payload(
            status="degraded",
            reason="not_computed",
            observations=observations,
            flows=flows,
            warnings=ingest
            + [
                _warning(
                    "NAV_DUPLICATE_DATE",
                    "error",
                    f"Two NAV observations claim {exc}; the series is ambiguous.",
                    date=str(exc),
                )
            ]
            + freshness,
            methodology=methodology,
            account_id=account_id,
            nav_source=nav_source,
            nav_sessions_behind=nav_sessions_behind,
        )
    except NonFiniteInput as exc:
        return _suppressed_payload(
            status="degraded",
            reason="not_computed",
            observations=observations,
            flows=flows,
            warnings=ingest + [_warning("NAV_NON_FINITE", "error", str(exc))] + freshness,
            methodology=methodology,
            account_id=account_id,
            nav_source=nav_source,
            nav_sessions_behind=nav_sessions_behind,
        )

    warnings: List[Dict[str, Any]] = ingest + freshness
    inferred = _inferred_candidates(chain)
    applied_flows = dict(flows.by_date)

    if inferred and allow_inferred_flows:
        for candidate in inferred:
            applied_flows[candidate["date"]] = applied_flows.get(candidate["date"], 0.0) + candidate["amount"]
        chain = build_subperiods(observations, applied_flows)
        methodology = _methodology(risk_free_rate, risk_free_source, inferred)
        warnings.append(
            _warning(
                "INFERRED_FLOW_CANDIDATE",
                "error",
                "Inferred external flows were APPLIED; these numbers are diagnostic, not published.",
                inferred_flows=inferred,
            )
        )
    else:
        for candidate in inferred:
            warnings.append(
                _warning(
                    "INFERRED_FLOW_CANDIDATE",
                    "info",
                    f"A flow of {candidate['amount']:+,.2f} on {candidate['date']} would explain "
                    "the excluded session; not applied.",
                    **candidate,
                )
            )

    sorted_observations = sorted(
        {o.date: o for o in observations}.values(), key=lambda o: o.date
    )
    placement = place_flows([o.date for o in sorted_observations], applied_flows)
    warnings.extend(_placement_warnings(placement))
    warnings.extend(_subperiod_warnings(chain))

    rf_daily = daily_risk_free(risk_free_rate)
    returns = list(chain.returns)
    n = chain.n_returns

    annualized = annualize_return(chain.cum_return, chain.calendar_days)
    if annualized.value is not None and abs(annualized.value) > twr_gates.IMPLAUSIBLE_ANNUALIZED:
        warnings.append(
            _warning(
                "IMPLAUSIBLE_ANNUALIZED",
                "error",
                f"Annualized return of {annualized.value:+.2%} is not a plausible portfolio result.",
                annualized=annualized.value,
            )
        )
        annualized = GatedValue(
            None, chain.calendar_days, twr_gates.MIN_CALENDAR_DAYS_ANNUALIZED, "implausible"
        )

    drawdown_result = drawdowns(chain)
    risk = {
        "volatility": volatility(returns).as_dict(),
        "sharpe_ratio": sharpe_ratio(returns, rf_daily).as_dict(),
        "sortino_ratio": sortino_ratio(returns, rf_daily).as_dict(),
        "downside_deviation": downside_deviation(returns).as_dict(),
        "max_drawdown": drawdown_result.max_drawdown.as_dict(),
        "current_drawdown": drawdown_result.current_drawdown.as_dict(),
        "var_95": historical_var(returns).as_dict(),
        "cvar_95": conditional_var(returns).as_dict(),
    }
    distribution = {
        name: gated.as_dict() for name, gated in return_distribution(returns).items()
    }

    block, benchmark_reason = _benchmark_block(
        chain, benchmark, benchmark_symbol, rf_daily
    )
    if benchmark is not None and block is None:
        warnings.append(
            _warning(
                "BENCHMARK_UNAVAILABLE",
                "info",
                f"{benchmark_symbol} statistics suppressed: {benchmark_reason}.",
                reason=benchmark_reason,
            )
        )
    if block is not None and abs(block.alpha_annualized) > twr_gates.IMPLAUSIBLE_ALPHA:
        # Suppress the BENCHMARK, not the return, at the same severity as any
        # other benchmark suppression. This was "error", and an error floors the
        # whole payload to degraded, which took TWR total, max drawdown and
        # Sharpe down with it -- none of which touch the benchmark. A bad
        # regression against SPY says nothing about a time-weighted return
        # computed from NAV and flows alone. Live on 2026-08-16: the real series
        # chained cleanly to +90.81% with zero quarantined sessions and the page
        # still rendered "--" because alpha came out at +124.56%.
        implausible_alpha = block.alpha_annualized
        block = None
        benchmark_reason = "implausible"
        warnings.append(
            _warning(
                "IMPLAUSIBLE_ALPHA",
                "info",
                f"Annualized alpha of {implausible_alpha:+.2%} is outside a plausible range; "
                f"{benchmark_symbol} statistics are suppressed. The return itself is unaffected.",
                alpha=implausible_alpha,
            )
        )

    benchmark_returns = returns_from_closes(benchmark) if benchmark else {}
    series = _series_points(
        sorted_observations,
        chain,
        benchmark if block is not None else None,
        benchmark_returns if block is not None else None,
    )

    first, last = sorted_observations[0], sorted_observations[-1]
    net_external_flows = sum(placement.by_subperiod.values())
    period_return = modified_dietz(
        first.nav,
        last.nav,
        sorted(placement.by_subperiod.items()),
        chain.period_start or first.date,
        chain.period_end or last.date,
        n_returns=n,
    )
    irr = _xirr_for(chain, sorted_observations, applied_flows)

    base_status = _base_status(chain)
    if base_status == "insufficient_data":
        warnings.append(
            _warning(
                "NAV_INSUFFICIENT",
                "info",
                f"Needs at least 2 usable NAV observations (have {chain.n_nav_observations}).",
                n_nav_observations=chain.n_nav_observations,
                n_returns=n,
            )
        )

    payload = _shell(
        status=_resolve_status(base_status, warnings),
        account_id=account_id,
        methodology=methodology,
        nav_source=nav_source,
        nav_as_of=chain.period_end,
        nav_sessions_behind=nav_sessions_behind,
        flows=flows,
        period_start=chain.period_start,
        period_end=chain.period_end,
        calendar_days=chain.calendar_days,
        counts={
            "n_nav_observations": chain.n_nav_observations,
            "n_subperiods": chain.n_subperiods,
            "n_returns": n,
            "n_skipped": sum(1 for sp in chain.subperiods if sp.skip_reason),
            "n_suspect": sum(1 for sp in chain.subperiods if sp.skip_reason == "suspect_no_flow"),
        },
        warnings=warnings,
    )
    payload["twr"] = {
        "cum_return": chain.cum_return,
        "annualized": annualized.as_dict(),
        "excludes_suspect": chain.excluded_suspect,
    }
    payload["mwr"] = {
        "period_return": period_return.as_dict(),
        "annualized": GatedValue(
            irr.value, n, twr_gates.MIN_N_MWR, irr.unavailable_reason
        ).as_dict(),
        "multiple_sign_changes": irr.multiple_sign_changes,
    }
    payload["risk"] = risk
    payload["distribution"] = distribution
    payload["drawdown_detail"] = {
        "trough_date": drawdown_result.trough_date,
        "peak_date": drawdown_result.peak_date,
        "trough_days": drawdown_result.trough_days,
        "recovery_days": drawdown_result.recovery_days,
        "ongoing": drawdown_result.ongoing,
    }
    payload["benchmark"] = block.as_dict() if block is not None else None
    payload["equity"] = {
        "starting": first.nav,
        "ending": last.nav,
        "net_external_flows": net_external_flows,
        "investment_pnl": last.nav - first.nav - net_external_flows,
    }
    payload["series"] = series
    payload["subperiods"] = _subperiod_rows(chain)
    return payload


def _base_status(chain: SubperiodChain) -> str:
    if chain.n_nav_observations < 2:
        return "insufficient_data"
    if chain.n_returns == 0 and not chain.excluded_suspect:
        return "insufficient_data"
    return "ok"


def _benchmark_block(
    chain: SubperiodChain,
    benchmark: Optional[Mapping[str, float]],
    symbol: str,
    rf_daily: float,
):
    if not benchmark:
        return None, "benchmark_unavailable"
    portfolio_returns = {sp.date: sp.ret for sp in chain.subperiods if sp.ret is not None}
    pairs = align_series(portfolio_returns, returns_from_closes(benchmark))
    return build_benchmark_block(
        pairs,
        rf_daily,
        n_returns=chain.n_returns,
        symbol=symbol,
        basis=BENCHMARK_BASIS,
    )


def _xirr_for(
    chain: SubperiodChain,
    observations: Sequence[NavObservation],
    flows: Mapping[str, float],
):
    return xirr(
        build_cashflow_vector(observations, flows), chain.calendar_days, chain.n_returns
    )


# ---------------------------------------------------------------------------
# persistence + CLI
# ---------------------------------------------------------------------------


def persist_payload(payload: Mapping[str, Any]) -> None:
    """Write data/performance.json and mirror to Turso performance_snapshots."""
    try:
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = _PERF_PATH.with_suffix(".tmp")
        tmp.write_text(_json.dumps(payload, indent=2))
        tmp.replace(_PERF_PATH)
        print(f"[perf_twr] Wrote {_PERF_PATH} as_of={payload.get('nav_as_of')}", file=_sys.stderr)
    except OSError as exc:
        print(f"[perf_twr] Failed to write {_PERF_PATH}: {exc}", file=_sys.stderr)

    if not _os.environ.get("TURSO_DB_URL") or not _os.environ.get("TURSO_AUTH_TOKEN"):
        return
    try:
        _os.environ.setdefault("RADON_DB_NO_REPLICA", "1")
        try:
            from db.writer import upsert_performance_snapshot  # type: ignore
        except ImportError:
            from scripts.db.writer import upsert_performance_snapshot  # type: ignore

        taken_at = str(payload.get("generated_at") or _now_iso())
        upsert_performance_snapshot(taken_at, dict(payload))
        print(f"[perf_twr] Mirrored to Turso performance_snapshots ({taken_at})", file=_sys.stderr)
    except Exception as exc:  # noqa: BLE001
        print(f"[perf_twr] Turso mirror failed: {exc}", file=_sys.stderr)

    _persist_perf_tables(payload)


def _nav_snapshot_rows(payload: Mapping[str, Any], account_id: str) -> List[Dict[str, Any]]:
    return [
        {"account_id": account_id, "report_date": point["date"], "total_net_liq": point["nav"]}
        for point in payload.get("series") or []
    ]


def _external_flow_rows(payload: Mapping[str, Any], account_id: str) -> List[Dict[str, Any]]:
    return [
        {
            "account_id": account_id,
            "report_date": row["date"],
            "amount": row["c"],
            "flow_type": "external",
            "note": payload.get("flows_source") or "",
        }
        for row in payload.get("subperiods") or []
        if row.get("c")
    ]


def _twr_subperiod_rows(payload: Mapping[str, Any], account_id: str) -> List[Dict[str, Any]]:
    return [
        {
            "account_id": account_id,
            "report_date": row["date"],
            "b": row["b"],
            "e": row["e"],
            "c": row["c"],
            "r": row["r"],
            "cum_r": row["cum_r"],
        }
        for row in payload.get("subperiods") or []
        if row.get("r") is not None and row.get("cum_r") is not None
    ]


def _persist_perf_tables(payload: Mapping[str, Any]) -> None:
    """Fill the three tables migration 0035 created: NAV, flows, subperiods.

    They are the audit trail for a published number; a snapshot blob alone
    cannot answer "which flow moved this session".
    """
    account_id = str(payload.get("account_id") or DEFAULT_ACCOUNT_ID)
    try:
        try:
            from db import writer  # type: ignore
        except ImportError:
            from scripts.db import writer  # type: ignore

        writer.upsert_nav_snapshot_rows(_nav_snapshot_rows(payload, account_id))
        writer.upsert_external_flow_rows(_external_flow_rows(payload, account_id))
        writer.upsert_twr_subperiod_rows(_twr_subperiod_rows(payload, account_id))
        print(
            "[perf_twr] Mirrored nav_snapshots / external_flows / twr_subperiods",
            file=_sys.stderr,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[perf_twr] TWR table mirror failed: {exc}", file=_sys.stderr)


def _apply_mirrored_flow_coverage(
    observations: Sequence[NavObservation], flows: FlowSet
) -> Tuple[Sequence[NavObservation], FlowSet, List[Dict[str, Any]]]:
    """Hold a mirrored-flow build to the sessions the mirror actually verified.

    Live Flex flows need no bound — the statement covers what it covers. A
    mirror does: publishing a session it never saw asserts "no external flow
    that day" on no evidence.
    """
    if flows.source != "turso" or not observations:
        return observations, flows, []

    covered_through = load_flows_coverage_through()
    bounded = bound_observations_to_coverage(observations, covered_through)
    if not bounded:
        return observations, FlowSet.failed(
            f"mirrored_flows_cover_nothing_through_{covered_through or 'unknown'}"
        ), []

    dropped = len(observations) - len(bounded)
    return (
        bounded,
        flows,
        [
            _warning(
                "FLOWS_SOURCE_MIRROR",
                # Provenance, not freshness — the same distinction NAV_SOURCE_*
                # draws. `warn` floors the payload to "stale", and the render
                # layer gates the TWR on "stale" exactly as on "degraded", so
                # declaring the mirror at `warn` would blank the very page this
                # fallback exists to keep alive. The mirror's age is policed by
                # the coverage bound below, not by the severity.
                "info",
                "External flows came from the last mirrored statement, not a live "
                f"Flex fetch; verified through {covered_through or 'unknown'}.",
                flows_source="turso",
                covered_through=covered_through,
                sessions_dropped=dropped,
            )
        ],
    )


def build_and_persist(*, persist: bool = True, allow_inferred_flows: bool = False) -> Dict[str, Any]:
    """Resolve NAV + flows, apply the gates, assemble and optionally persist."""
    resolution = get_nav_snapshots()
    observations = [
        NavObservation(date=d, nav=float(v)) for d, v in sorted(resolution.by_date.items())
    ]
    if observations:
        flows, coverage = resolve_flows(resolution.document)
    else:
        flows, coverage = FlowSet.failed("no_nav_series"), []

    observations, flows, mirror_warnings = _apply_mirrored_flow_coverage(observations, flows)
    coverage = list(coverage) + mirror_warnings

    benchmark = None
    if len(observations) >= 2:
        benchmark = load_benchmark_closes(observations[0].date, observations[-1].date) or None

    rf_rate, rf_warning = get_risk_free_rate(as_of=observations[-1].date) if observations else (0.0, "no nav")
    rf_source = "fallback_zero" if rf_warning else "fred_dgs3mo"

    payload = build_payload(
        observations,
        flows,
        benchmark=benchmark,
        risk_free_rate=float(rf_rate or 0.0),
        risk_free_source=rf_source,
        nav_source=resolution.source,
        nav_sessions_behind=sessions_behind(observations[-1].date) if observations else 0,
        allow_inferred_flows=allow_inferred_flows,
        ingest_warnings=_account_gap_warnings(resolution.account_gaps) + coverage,
    )
    if persist:
        persist_payload(payload)
    return payload


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="TWR builder (Flex EquitySummaryInBase + flows)")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the v2 payload on stdout (what the FastAPI bridge reads)",
    )
    parser.add_argument(
        "--no-persist",
        action="store_true",
        help="Skip data/performance.json and the Turso mirror",
    )
    parser.add_argument("--check", action="store_true", help="Exit 2 unless status is ok or stale")
    parser.add_argument(
        "--allow-inferred-flows",
        action="store_true",
        help="Apply inferred flows for quarantined sessions (diagnostic; forces degraded)",
    )
    args = parser.parse_args()

    payload = build_and_persist(
        persist=not args.no_persist, allow_inferred_flows=args.allow_inferred_flows
    )
    summary = (
        f"[perf_twr] status={payload['status']} "
        f"period={payload['period_start']}..{payload['period_end']} "
        f"source={payload['nav_source']} flows={payload['flows_status']}"
    )
    # stdout carries the result JSON and nothing else (scripts/CLAUDE.md).
    print(summary, file=_sys.stderr)
    if args.json:
        print(_json.dumps(payload, indent=2))
    if args.check and payload["status"] not in ("ok", "stale"):
        _sys.exit(2)


if __name__ == "__main__":
    main()
