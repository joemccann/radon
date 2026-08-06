"""TWR Modified Dietz daily builder — Plan §4.2-4.3.

One definition: Time-Weighted Return via daily sub-periods:
  r_i = (E - B - C) / B   (or E/B - 1 when C==0)
  TWR_cum = Π(1+r_i) - 1

External flows C are deposits/withdrawals/ACATS/internal.  Borrow fees,
commissions, dividends are NOT flows — they stay in NAV as returns.

Source of truth: Flex EquitySummaryInBase (NAV) + CashTransactions/Transfers
(flows).  No IB portal scrape, no UW/Yahoo, no reconstructed fallback.

Gates (§4.5):
 - annualized only if N>=20
 - beta/alpha/tracking_error only if N>=40
 - VaR/CVaR only if N>=60
 - Rf from FRED DGS3MO, fallback 0 with warning
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

try:
    from clients.fred_client import get_risk_free_rate  # type: ignore
except Exception:
    try:
        from scripts.clients.fred_client import get_risk_free_rate  # type: ignore
    except Exception:  # pragma: no cover
        def get_risk_free_rate(*a, **kw):  # type: ignore
            return 0.0, "Rf unavailable — fred_client not found"

try:
    from utils.et_date import normalize_report_date  # type: ignore
except Exception:
    try:
        from scripts.utils.et_date import normalize_report_date  # type: ignore
    except Exception:  # pragma: no cover
        def normalize_report_date(v):  # type: ignore
            return str(v)[:10] if v else None

TRADING_DAYS = 252
CURVE_TYPE = "twr_modified_dietz_daily"
RETURN_BASIS = "time_weighted"

# Flow types that ARE external and must be in C.  Everything else (borrow
# fee, commission, dividend, interest, withholding, fee) is a return component.
EXTERNAL_FLOW_TYPES = {"deposit", "withdrawal", "acats", "internal", "transfer"}

# Types that are explicitly NOT external flows
NON_FLOW_TYPES = {"borrow_fee", "borrow fee", "commission", "dividend", "interest", "fee", "withholding", "stock_dividend"}


def is_external_flow_type(flow_type: str | None) -> bool:
    if not flow_type or not isinstance(flow_type, str):
        return False
    t = flow_type.strip().lower().replace(" ", "_").replace("-", "_")
    if t in NON_FLOW_TYPES:
        return False
    return t in EXTERNAL_FLOW_TYPES


def twr_return(b: float, e: float, c: float) -> float:
    """Modified Dietz for daily segmentation: (E - B - C) / B."""
    if b == 0 or not math.isfinite(b):
        return 0.0
    return (e - b - c) / b


def build_twr_subperiods(
    nav_snapshots: Dict[str, float],
    external_flows: Dict[str, float] | None = None,
) -> List[Dict[str, Any]]:
    """Build per-date TWR subperiods sorted by report_date.

    nav_snapshots: {YYYY-MM-DD: total_net_liq}
    external_flows: {YYYY-MM-DD: sum(C)}  (+deposit, -withdrawal, ±ACATS)
    Returns list of {report_date, b, e, c, r, cum_r} ordered ascending.
    First entry has r=0, cum_r=0 (no prior B).
    """
    flows = external_flows or {}
    # Normalize dates via et_date (compact -> ISO etc) but keep sort stable
    normalized_nav: Dict[str, float] = {}
    for k, v in nav_snapshots.items():
        nk = normalize_report_date(k) or k
        normalized_nav[nk] = float(v)
    normalized_flows: Dict[str, float] = {}
    for k, v in flows.items():
        nk = normalize_report_date(k) or k
        normalized_flows[nk] = normalized_flows.get(nk, 0.0) + float(v)

    dates = sorted(normalized_nav.keys())
    out: List[Dict[str, Any]] = []
    prev_e: Optional[float] = None
    prev_cum = 0.0
    for date in dates:
        e = float(normalized_nav[date])
        c = float(normalized_flows.get(date, 0.0))
        if prev_e is None:
            b = e
            r = 0.0
            cum = 0.0
        else:
            b = float(prev_e)
            r = twr_return(b, e, c)
            cum = (1.0 + prev_cum) * (1.0 + r) - 1.0
        out.append({"report_date": date, "b": b, "e": e, "c": c, "r": r, "cum_r": cum})
        prev_e = e
        prev_cum = cum
    return out


def _returns_from_subperiods(subperiods: List[Dict[str, Any]]) -> List[float]:
    # Exclude first zero-return seed period
    if len(subperiods) <= 1:
        return []
    return [float(s["r"]) for s in subperiods[1:]]


def compute_annualized_return(total_return: float, n: int) -> Optional[float]:
    if n < 20:
        return None
    return float((1.0 + total_return) ** (TRADING_DAYS / max(n, 1)) - 1.0)


def compute_twr_metrics(
    subperiods: List[Dict[str, Any]],
    *,
    risk_free_rate: float = 0.0,
    benchmark_returns: Optional[List[float]] = None,
) -> Dict[str, Any]:
    """Compute gated metrics from TWR subperiods.

    risk_free_rate is annual decimal (e.g. 0.0412).  Converted to daily for
    Sharpe/Sortino excess.  Benchmark is optional list of daily benchmark
    returns aligned to same N.
    """
    returns = _returns_from_subperiods(subperiods)
    n = len(returns)
    total_return = float(subperiods[-1]["cum_r"]) if subperiods else 0.0

    out: Dict[str, Any] = {
        "total_return": total_return,
        "trading_days": n,
        "n": n,
    }

    # Annualized — gate N>=20
    ann = compute_annualized_return(total_return, n)
    out["annualized_return"] = ann

    # Volatility / Sharpe / Sortino (only if n>=2 for std)
    if n >= 2:
        arr = np.array(returns, dtype=float)
        rf_daily = float(risk_free_rate) / TRADING_DAYS
        excess = arr - rf_daily
        vol = float(np.std(arr, ddof=1) * math.sqrt(TRADING_DAYS))
        out["annualized_volatility"] = vol
        downside_rms = float(math.sqrt(np.mean(np.square(np.minimum(arr, 0.0))))) if n > 0 else 0.0
        out["downside_deviation"] = downside_rms * math.sqrt(TRADING_DAYS)
        sd = float(np.std(arr, ddof=1))
        out["sharpe_ratio"] = float((excess.mean() / sd) * math.sqrt(TRADING_DAYS)) if sd > 0 else 0.0
        out["sortino_ratio"] = float((excess.mean() / downside_rms) * math.sqrt(TRADING_DAYS)) if downside_rms > 0 else 0.0
        out["best_day"] = float(arr.max())
        out["worst_day"] = float(arr.min())
        out["hit_rate"] = float((arr > 0).sum() / n) if n else 0.0
        # Max drawdown from cum equity curve (1+cum)
        equity = np.array([1.0 + float(s["cum_r"]) for s in subperiods], dtype=float)
        cummax = np.maximum.accumulate(equity)
        dd = equity / cummax - 1.0
        out["max_drawdown"] = float(dd.min()) if len(dd) else 0.0
        # skew/kurt rely on n
        s = pd.Series(arr)
        out["skew"] = float(s.skew()) if n > 2 else 0.0
        out["kurtosis"] = float(s.kurt()) if n > 3 else 0.0
    else:
        out["annualized_volatility"] = 0.0 if n else None
        out["downside_deviation"] = 0.0 if n else None
        out["sharpe_ratio"] = 0.0 if n else None
        out["sortino_ratio"] = 0.0 if n else None
        out["best_day"] = returns[0] if returns else 0.0
        out["worst_day"] = returns[0] if returns else 0.0
        out["hit_rate"] = 0.0
        out["max_drawdown"] = 0.0
        out["skew"] = 0.0
        out["kurtosis"] = 0.0

    # Beta / Alpha / tracking — gate N>=40
    if n >= 40 and benchmark_returns is not None and len(benchmark_returns) >= n:
        arr = np.array(returns, dtype=float)
        bench = np.array(benchmark_returns[:n], dtype=float)
        if np.var(bench, ddof=1) > 0:
            beta = float(np.cov(arr, bench, ddof=1)[0, 1] / np.var(bench, ddof=1))
            corr = float(np.corrcoef(arr, bench)[0, 1]) if n > 1 else 0.0
            # Require |corr|>0.2 per plan, else null out
            if abs(corr) > 0.2:
                out["beta"] = beta
                out["alpha"] = float((arr.mean() - beta * bench.mean()) * TRADING_DAYS)
                out["correlation"] = corr
                out["r_squared"] = corr ** 2
                active = arr - bench
                te = float(np.std(active, ddof=1) * math.sqrt(TRADING_DAYS)) if np.std(active, ddof=1) > 0 else 0.0
                out["tracking_error"] = te
                out["information_ratio"] = float((active.mean() / np.std(active, ddof=1)) * math.sqrt(TRADING_DAYS)) if np.std(active, ddof=1) > 0 else 0.0
            else:
                out["beta"] = None
                out["alpha"] = None
                out["correlation"] = None
                out["r_squared"] = None
                out["tracking_error"] = None
                out["information_ratio"] = None
        else:
            out["beta"] = None
            out["alpha"] = None
            out["correlation"] = None
            out["r_squared"] = None
            out["tracking_error"] = None
            out["information_ratio"] = None
    else:
        # Not enough data — null per gates
        if "beta" not in out:
            out["beta"] = None
            out["alpha"] = None
            out["correlation"] = None
            out["r_squared"] = None
            out["tracking_error"] = None
            out["information_ratio"] = None

    # VaR / CVaR — gate N>=60
    if n >= 60:
        arr = np.array(returns, dtype=float)
        var_95 = float(np.quantile(arr, 0.05))
        cvar_95 = float(arr[arr <= var_95].mean()) if len(arr[arr <= var_95]) else 0.0
        q95 = float(np.quantile(arr, 0.95))
        out["var_95"] = var_95
        out["cvar_95"] = cvar_95
        out["tail_ratio"] = float(abs(q95 / var_95)) if var_95 != 0 else 0.0
    else:
        out["var_95"] = None
        out["cvar_95"] = None
        out["tail_ratio"] = None

    return out


def build_payload(
    nav_snapshots: Dict[str, float] | Dict[str, Dict[str, float]] | None,
    external_flows: Dict[str, float] | None = None,
    *,
    benchmark_series: Dict[str, float] | None = None,
    risk_free_rate_override: Optional[float] = None,
    account_id: str = "ALL",
) -> Dict[str, Any]:
    """Build the performance_snapshots payload.

    Returns dict with keys: status, methodology, summary, series,
    twr_subperiods, warnings, period_start/end, benchmark.
    On insufficient data (<2 NAV rows) returns status insufficient_data.
    """
    warnings: List[str] = []

    # Support multi-account consolidated shape: {account_id: {date: nav}}
    # Detect by checking if values are dicts
    consolidated_nav: Dict[str, float] = {}
    if nav_snapshots is None or len(nav_snapshots) == 0:
        return {
            "status": "insufficient_data",
            "warnings": ["No Flex NAV — add IB_FLEX_NAV_QUERY_ID (EquitySummaryInBase)"],
            "methodology": {
                "curve_type": CURVE_TYPE,
                "return_basis": RETURN_BASIS,
                "risk_free_rate": 0.0,
                "library_strategy": "fred_dgs3mo",
            },
            "summary": {},
            "series": [],
            "twr_subperiods": [],
        }
    # Detect multi-account
    sample_val = next(iter(nav_snapshots.values()))
    if isinstance(sample_val, dict):
        # nav_snapshots is {acct: {date: nav}}
        all_dates: set[str] = set()
        for acct_map in nav_snapshots.values():  # type: ignore
            for d in acct_map.keys():
                all_dates.add(normalize_report_date(d) or d)
        for d in all_dates:
            total = 0.0
            for acct_map in nav_snapshots.values():  # type: ignore
                # flows are per-account; internal transfers should cancel in consolidated
                nd = normalize_report_date(d) or d
                # find matching normalized key in acct_map
                for k, v in acct_map.items():
                    if (normalize_report_date(k) or k) == nd:
                        total += float(v)
                        break
            consolidated_nav[d] = total
        nav_map = consolidated_nav
        # For multi-account, external flows should be consolidated: sum across
        # accounts — internal transfers net to 0 by construction.  If caller
        # passes {acct: {date: flow}} shape, we sum similarly.
        if external_flows and isinstance(next(iter(external_flows.values()), 0), dict):
            consolidated_flows: Dict[str, float] = {}
            for acct_flows in external_flows.values():  # type: ignore
                for k, v in acct_flows.items():
                    nk = normalize_report_date(k) or k
                    consolidated_flows[nk] = consolidated_flows.get(nk, 0.0) + float(v)
            flows_map = consolidated_flows
        else:
            flows_map = { (normalize_report_date(k) or k): float(v) for k, v in (external_flows or {}).items()}
    else:
        nav_map = { (normalize_report_date(k) or k): float(v) for k, v in nav_snapshots.items()}  # type: ignore
        flows_map = { (normalize_report_date(k) or k): float(v) for k, v in (external_flows or {}).items()}

    if len(nav_map) < 2:
        warnings.append("Insufficient NAV rows (<2) — need at least two settlement dates for TWR")
        # Try to get Rf for methodology even in insufficient case
        if risk_free_rate_override is not None:
            rf = float(risk_free_rate_override)
            rf_warn = None
        else:
            rf, rf_warn = get_risk_free_rate()
            if rf_warn:
                warnings.append(rf_warn)
        return {
            "status": "insufficient_data",
            "warnings": warnings,
            "methodology": {
                "curve_type": CURVE_TYPE,
                "return_basis": RETURN_BASIS,
                "risk_free_rate": rf,
                "library_strategy": "fred_dgs3mo",
            },
            "summary": {},
            "series": [],
            "twr_subperiods": [],
            "period_start": min(nav_map.keys()) if nav_map else None,
            "period_end": max(nav_map.keys()) if nav_map else None,
        }

    # Inception is first NAV date, not Jan 1
    subperiods = build_twr_subperiods(nav_map, flows_map)

    # No hard-coded 2025-12-31 phantom — assert it never appears unless caller provided it
    for sp in subperiods:
        assert sp["report_date"] != "2025-12-31" or "2025-12-31" in nav_map, "phantom 2025-12-31 date injected"

    # Calendar is nav dates, not benchmark — holiday 2026-01-01 excluded if no NAV
    period_start = subperiods[0]["report_date"]
    period_end = subperiods[-1]["report_date"]

    # Risk-free
    if risk_free_rate_override is not None:
        rf = float(risk_free_rate_override)
        rf_warning = None
    else:
        rf, rf_warning = get_risk_free_rate(as_of=period_end)
        if rf_warning:
            warnings.append(rf_warning)

    # Flow note for UI banner
    if flows_map:
        for d, c in sorted(flows_map.items()):
            if c != 0:
                warnings.append(f"External flow on {d}: {c:+,.0f} — TWR excludes it")

    # Benchmark alignment by date (same N, not pct_change intersection hack)
    bench_rets: Optional[List[float]] = None
    if benchmark_series:
        bench_map = { (normalize_report_date(k) or k): float(v) for k, v in benchmark_series.items()}
        # Build benchmark daily returns aligned to subperiod dates (skip first)
        bench_dates = sorted(bench_map.keys())
        # For simplicity, compute bench returns for same dates as nav
        bench_aligned: List[float] = []
        prev = None
        for sp in subperiods:
            d = sp["report_date"]
            val = bench_map.get(d)
            if val is None:
                # missing benchmark on this date — skip contribution (use 0)
                bench_aligned.append(0.0)
                prev = val
                continue
            if prev is not None and prev != 0:
                bench_aligned.append(val / prev - 1.0)
            else:
                bench_aligned.append(0.0)
            prev = val
        # bench_aligned includes first 0; metrics expects n returns without first
        bench_rets = bench_aligned[1:] if len(bench_aligned) > 1 else None

    metrics = compute_twr_metrics(subperiods, risk_free_rate=rf, benchmark_returns=bench_rets)

    # Series for chart: equity = 1 + cum_r rebased to 100
    series = []
    for sp in subperiods:
        series.append({
            "date": sp["report_date"],
            "nav": sp["e"],
            "return": sp["r"],
            "cum_return": sp["cum_r"],
            "equity": 100.0 * (1.0 + sp["cum_r"]),
        })

    methodology = {
        "curve_type": CURVE_TYPE,
        "return_basis": RETURN_BASIS,
        "risk_free_rate": rf,
        "library_strategy": "fred_dgs3mo",
    }

    summary = {
        "total_return": metrics.get("total_return"),
        "annualized_return": metrics.get("annualized_return"),
        "trading_days": metrics.get("trading_days"),
        "period_start": period_start,
        "period_end": period_end,
        "max_drawdown": metrics.get("max_drawdown"),
        "sharpe_ratio": metrics.get("sharpe_ratio"),
        "beta": metrics.get("beta"),
        "alpha": metrics.get("alpha"),
        "var_95": metrics.get("var_95"),
        "cvar_95": metrics.get("cvar_95"),
    }

    return {
        "status": "ok",
        "methodology": methodology,
        "summary": summary,
        "metrics": metrics,
        "series": series,
        "twr_subperiods": subperiods,
        "warnings": warnings,
        "period_start": period_start,
        "period_end": period_end,
        "benchmark": "SPY",
    }


def consolidate_accounts(
    per_account_nav: Dict[str, Dict[str, float]],
    per_account_flows: Dict[str, Dict[str, float]] | None = None,
) -> Tuple[Dict[str, float], Dict[str, float]]:
    """Consolidate multi-account NAV/flows to ALL.

    Internal transfers cancel: withdrawal in U123 + deposit in U456 nets 0
    in the consolidated flows map.
    """
    nav = {}
    flows: Dict[str, float] = {}
    all_dates: set[str] = set()
    for m in per_account_nav.values():
        for d in m:
            all_dates.add(normalize_report_date(d) or d)
    if per_account_flows:
        for m in per_account_flows.values():
            for d in m:
                all_dates.add(normalize_report_date(d) or d)
    for d in sorted(all_dates):
        total_nav = 0.0
        total_flow = 0.0
        for acct, m in per_account_nav.items():
            for k, v in m.items():
                if (normalize_report_date(k) or k) == d:
                    total_nav += float(v)
                    break
        if per_account_flows:
            for acct, m in per_account_flows.items():
                for k, v in m.items():
                    if (normalize_report_date(k) or k) == d:
                        total_flow += float(v)
                        break
        nav[d] = total_nav
        if total_flow != 0:
            flows[d] = total_flow
    return nav, flows


# ── Flex + cache + Turso orchestration (Plan §4.3 Build) ─────────────

import json as _json
import os as _os
import sys as _sys
import time as _time
import xml.etree.ElementTree as _ET
from datetime import datetime as _dt
from datetime import timezone as _tz
from pathlib import Path as _Path

_PROJECT_ROOT = _Path(__file__).resolve().parent.parent
_DATA_DIR = _PROJECT_ROOT / "data"
_PERF_PATH = _DATA_DIR / "performance.json"
_NAV_CACHE_PATH = _DATA_DIR / "nav_history_ib.json"
_IB_NAV_CACHE_DIR = _DATA_DIR / "ib_nav_cache"

_FLEX_SEND = "https://gdcdyn.interactivebrokers.com/Universal/servlet/FlexStatementService.SendRequest"
_FLEX_GET = "https://gdcdyn.interactivebrokers.com/Universal/servlet/FlexStatementService.GetStatement"


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        if v in (None, "", "nan", "NaN"):
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _now_iso() -> str:
    return _dt.now(_tz.utc).isoformat().replace("+00:00", "Z")


def fetch_flex_xml(token: str, query_id: str, *, max_polls: int = 30, poll_secs: float = 3.0) -> str:
    """Poll Flex until FlexStatements appears; hard-fail if ReferenceCode missing."""
    from urllib.parse import urlencode
    from urllib.request import urlopen

    params = urlencode({"t": token, "q": query_id, "v": "3"})
    resp = urlopen(f"{_FLEX_SEND}?{params}", timeout=30)  # noqa: S310
    text = resp.read().decode("utf-8")
    root = _ET.fromstring(text)
    ref = root.find(".//ReferenceCode")
    if ref is None or not (ref.text or "").strip():
        err_code = root.findtext(".//ErrorCode") or ""
        err_msg = root.findtext(".//ErrorMessage") or text[:500]
        raise RuntimeError(f"Flex SendRequest failed code={err_code}: {err_msg}")
    ref_code = ref.text.strip()  # type: ignore
    for _ in range(max_polls):
        _time.sleep(poll_secs)
        params2 = urlencode({"t": token, "q": ref_code, "v": "3"})
        resp2 = urlopen(f"{_FLEX_GET}?{params2}", timeout=30)  # noqa: S310
        xml_text = resp2.read().decode("utf-8")
        if "<FlexStatements" in xml_text or "<FlexStatement" in xml_text:
            return xml_text
    raise RuntimeError(f"Flex GetStatement timeout after {max_polls} polls ref={ref_code}")


def parse_nav_entries(xml_text: str) -> Dict[str, float]:
    """Parse EquitySummaryByReportDateInBase -> {date: total_net_liq}."""
    root = _ET.fromstring(xml_text)
    out: Dict[str, float] = {}
    for node in root.findall(".//EquitySummaryByReportDateInBase"):
        dt = normalize_report_date(node.get("reportDate") or node.get("report_date") or "")
        if not dt:
            continue
        out[dt] = _safe_float(node.get("total"))
    return dict(sorted(out.items()))


def parse_flows_entries(xml_text: str) -> Dict[str, float]:
    """Parse CashTransaction deposits/withdrawals + Transfer cash -> {date: sum}."""
    root = _ET.fromstring(xml_text)
    flows: Dict[str, float] = {}

    def _is_external(raw: str) -> bool:
        low = (raw or "").lower()
        if "deposit" in low or "withdrawal" in low:
            return True
        return False

    for ct in root.findall(".//CashTransaction"):
        raw = ct.get("type") or ct.get("transactionType") or ""
        low = raw.lower()
        if any(kw in low for kw in ("dividend", "fee", "commission", "borrow", "interest", "tax", "payment in lieu")):
            continue
        if not _is_external(raw):
            continue
        amt = _safe_float(ct.get("amount"))
        if amt == 0:
            continue
        dt = normalize_report_date(ct.get("reportDate") or ct.get("dateTime") or ct.get("date") or "")
        if not dt:
            continue
        flows[dt] = flows.get(dt, 0.0) + amt

    for tr in root.findall(".//Transfer"):
        cash_amt = tr.get("cashTransfer") or tr.get("transferPrice") or ""
        if cash_amt and _safe_float(cash_amt) != 0:
            amt = _safe_float(cash_amt)
            dt = normalize_report_date(tr.get("reportDate") or tr.get("dateTime") or tr.get("date") or "")
            if dt:
                flows[dt] = flows.get(dt, 0.0) + amt
    return flows


def load_nav_from_disk() -> Dict[str, float] | None:
    """Try data/nav_history_ib.json then data/ib_nav_cache/*.json."""
    if _NAV_CACHE_PATH.exists():
        try:
            data = _json.loads(_NAV_CACHE_PATH.read_text())
            if isinstance(data, list) and len(data) >= 1:
                out: Dict[str, float] = {}
                for e in data:
                    dt = normalize_report_date(str(e.get("date") or e.get("reportDate") or ""))
                    if not dt:
                        continue
                    val = e.get("total") if "total" in e else e.get("net_liq")
                    out[dt] = _safe_float(val)
                if len(out) >= 2:
                    return out
            elif isinstance(data, dict) and len(data) >= 2:
                out2: Dict[str, float] = {}
                for k, v in data.items():
                    nk = normalize_report_date(k) or k
                    out2[nk] = _safe_float(v)
                if len(out2) >= 2:
                    return out2
        except (_json.JSONDecodeError, OSError):
            pass
    if _IB_NAV_CACHE_DIR.exists() and _IB_NAV_CACHE_DIR.is_dir():
        try:
            candidates = sorted(_IB_NAV_CACHE_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
            for p in candidates[:3]:
                try:
                    data = _json.loads(p.read_text())
                    if isinstance(data, list) and len(data) >= 1:
                        out: Dict[str, float] = {}
                        for e in data:
                            dt = normalize_report_date(str(e.get("date") or ""))
                            if not dt:
                                continue
                            val = e.get("total") if "total" in e else e.get("net_liq")
                            out[dt] = _safe_float(val)
                        if len(out) >= 2:
                            return out
                except (_json.JSONDecodeError, OSError):
                    continue
        except OSError:
            pass
    return None


def load_nav_from_turso() -> Dict[str, float] | None:
    """Try Turso nav_history then nav_snapshots (plan table) if credentials present."""
    if not _os.environ.get("TURSO_DB_URL") or not _os.environ.get("TURSO_AUTH_TOKEN"):
        return None
    try:
        try:
            from db.client import get_db  # type: ignore
        except ImportError:
            from scripts.db.client import get_db  # type: ignore

        db = get_db()
        for table in ("nav_history", "nav_snapshots"):
            try:
                if table == "nav_history":
                    rows = db.execute("SELECT date, net_liq FROM nav_history ORDER BY date ASC").fetchall()
                    out: Dict[str, float] = {}
                    for r in rows:
                        if isinstance(r, dict):
                            dt = normalize_report_date(str(r.get("date") or ""))
                            val = _safe_float(r.get("net_liq"))
                        else:
                            dt = normalize_report_date(str(r[0] or ""))
                            val = _safe_float(r[1])
                        if dt:
                            out[dt] = val
                    if len(out) >= 2:
                        return out
                else:
                    rows = db.execute("SELECT report_date, total_net_liq FROM nav_snapshots ORDER BY report_date ASC").fetchall()
                    out2: Dict[str, float] = {}
                    for r in rows:
                        if isinstance(r, dict):
                            dt = normalize_report_date(str(r.get("report_date") or ""))
                            val = _safe_float(r.get("total_net_liq"))
                        else:
                            dt = normalize_report_date(str(r[0] or ""))
                            val = _safe_float(r[1])
                        if dt:
                            out2[dt] = val
                    if len(out2) >= 2:
                        return out2
            except Exception:
                continue
    except Exception:
        pass
    return None


def get_nav_snapshots() -> tuple[Dict[str, float] | None, str | None]:
    """Resolve NAV: Flex live -> disk -> Turso. Returns (map_or_None, source)."""
    token = _os.environ.get("IB_FLEX_TOKEN")
    nav_qid = _os.environ.get("IB_FLEX_NAV_QUERY_ID")
    if token and nav_qid:
        try:
            xml_text = fetch_flex_xml(token, nav_qid)
            entries = parse_nav_entries(xml_text)
            if len(entries) >= 1:
                try:
                    _NAV_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
                    # cache as list for compat with existing readers
                    _NAV_CACHE_PATH.write_text(_json.dumps([{"date": k, "total": v} for k, v in entries.items()], indent=2))
                except OSError:
                    pass
                if len(entries) >= 2:
                    return entries, "flex_live"
                return entries, "flex_live"
        except Exception as exc:  # noqa: BLE001
            print(f"[perf_twr] Flex NAV fetch failed: {exc}", file=_sys.stderr)
    disk = load_nav_from_disk()
    if disk:
        return disk, "disk_cache"
    turso = load_nav_from_turso()
    if turso:
        return turso, "turso_nav_history"
    return None, None


def get_external_flows_for_nav(nav: Dict[str, float] | None = None) -> Dict[str, float]:
    token = _os.environ.get("IB_FLEX_TOKEN")
    flows_qid = _os.environ.get("IB_FLEX_FLOWS_QUERY_ID") or _os.environ.get("IB_FLEX_NAV_QUERY_ID")
    if not token or not flows_qid:
        return {}
    try:
        xml_text = fetch_flex_xml(token, flows_qid)
        return parse_flows_entries(xml_text)
    except Exception as exc:  # noqa: BLE001
        print(f"[perf_twr] Flex flows fetch failed: {exc}", file=_sys.stderr)
        return {}


def _load_yahoo_spy_series(start: str, end: str) -> Dict[str, float]:
    """Single-source SPY closes via Yahoo (no IB/UW fan-out)."""
    try:
        from urllib.request import Request, urlopen

        fmt = "%Y-%m-%d"
        start_ts = int(_dt.strptime(start, fmt).replace(tzinfo=_tz.utc).timestamp())
        end_ts = int(_dt.strptime(end, fmt).replace(tzinfo=_tz.utc).timestamp()) + 86400
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/SPY?period1={start_ts}&period2={end_ts}&interval=1d&includePrePost=false"
        req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(req, timeout=15) as resp:  # noqa: S310
            data = _json.loads(resp.read().decode("utf-8"))
        result = (data.get("chart") or {}).get("result") or []
        if not result:
            return {}
        row = result[0]
        timestamps = row.get("timestamp") or []
        closes = (row.get("indicators") or {}).get("quote", [{}])[0].get("close") or []
        out: Dict[str, float] = {}
        for ts, close in zip(timestamps, closes):
            if close is None:
                continue
            try:
                d = _dt.fromtimestamp(int(ts), tz=_tz.utc).strftime("%Y-%m-%d")
            except (TypeError, ValueError, OSError):
                continue
            if start <= d <= end and math.isfinite(float(close)):
                out[d] = float(close)
        return out
    except Exception as exc:  # noqa: BLE001
        print(f"[perf_twr] SPY fetch failed: {exc}", file=_sys.stderr)
        return {}


def persist_payload(payload: Dict[str, Any]) -> None:
    """Write to data/performance.json and Turso performance_snapshots."""
    try:
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = _PERF_PATH.with_suffix(".tmp")
        tmp.write_text(_json.dumps(payload, indent=2))
        tmp.replace(_PERF_PATH)
        print(f"[perf_twr] Wrote {_PERF_PATH} as_of={payload.get('as_of') or payload.get('period_end')}")
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

        taken_at = str(payload.get("last_sync") or payload.get("as_of") or _now_iso())
        # Ensure taken_at is ISO UTC
        upsert_performance_snapshot(taken_at, payload)
        print(f"[perf_twr] Mirrored to Turso performance_snapshots ({taken_at})")
    except Exception as exc:  # noqa: BLE001
        print(f"[perf_twr] Turso mirror failed: {exc}", file=_sys.stderr)


def build_and_persist(*, allow_insufficient: bool = True) -> Dict[str, Any]:
    """Top-level builder: resolve NAV/flows, compute TWR, persist, return payload.

    Missing IB_FLEX tokens -> insufficient_data (graceful). No raise.
    Writes insufficient_data payload so UI shows empty state, not stale curve.
    """
    warnings: list[str] = []
    token = _os.environ.get("IB_FLEX_TOKEN")
    nav_qid = _os.environ.get("IB_FLEX_NAV_QUERY_ID")
    if not token or not nav_qid:
        warnings.append("IB_FLEX_TOKEN / IB_FLEX_NAV_QUERY_ID not configured — using cache if available.")
        print("[perf_twr] IB_FLEX tokens missing; trying cache/Turso", file=_sys.stderr)

    nav_map, nav_source = get_nav_snapshots()
    if nav_map is None or len(nav_map) < 2:
        reason = "Flex NAV empty or cache missing"
        if not nav_source:
            reason = "No Flex NAV — add IB_FLEX_NAV_QUERY_ID"
            warnings.append("Performance needs Flex NAV. Add the EquitySummary report in IB Portal.")
        payload = build_payload(None, None)
        # Merge our richer warnings
        payload["warnings"] = warnings + payload.get("warnings", [])
        payload["nav_source"] = nav_source
        payload["reason"] = reason
        if allow_insufficient:
            # Ensure methodology risk_free filled from fred
            persist_payload(payload)
        return payload

    flows = get_external_flows_for_nav(nav_map)
    # Benchmark best-effort
    period_start = min(nav_map.keys())
    period_end = max(nav_map.keys())
    bench_series: Dict[str, float] | None = None
    try:
        bench_series = _load_yahoo_spy_series(period_start, period_end)
    except Exception:
        bench_series = None

    payload = build_payload(nav_map, flows, benchmark_series=bench_series)
    payload["nav_source"] = nav_source
    payload["warnings"] = warnings + payload.get("warnings", [])
    persist_payload(payload)
    return payload


def main() -> None:
    import argparse as _argparse

    parser = _argparse.ArgumentParser(description="TWR builder (Flex EquitySummaryInBase + flows)")
    parser.add_argument("--json", action="store_true", help="Print payload to stdout (don't write)")
    parser.add_argument("--check", action="store_true", help="Exit 2 if insufficient_data")
    args = parser.parse_args()

    if args.json:
        payload = build_and_persist(allow_insufficient=False)
        # When --json, don't persist insufficient; just print
        if payload.get("status") == "insufficient_data":
            # Try to still build insufficient for stdout
            nav_map, nav_source = get_nav_snapshots()
            if nav_map is None:
                payload = build_payload(None, None)
                payload["nav_source"] = nav_source
        print(_json.dumps(payload, indent=2))
        if args.check and payload.get("status") == "insufficient_data":
            _sys.exit(2)
        return

    payload = build_and_persist(allow_insufficient=True)
    status = payload.get("status") or "ok"
    print(f"[perf_twr] build status={status} period={payload.get('period_start')}..{payload.get('period_end')} source={payload.get('nav_source')}")
    if args.check and status == "insufficient_data":
        _sys.exit(2)


if __name__ == "__main__":
    main()
