#!/usr/bin/env python3
"""Generate an HTML explainer for every rendered item on the /performance page."""

from __future__ import annotations

import argparse
import html
import json
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List


ROOT = Path(__file__).resolve().parent.parent
DATA_PATH = ROOT / "data" / "performance.json"
CHART_SYSTEM_PATH = ROOT / "web" / "lib" / "chart-system-spec.json"
REPORTS_DIR = ROOT / "reports"
DEFAULT_OUTPUT = REPORTS_DIR / f"performance-page-explainer-{datetime.now().strftime('%Y-%m-%d')}.html"

BRAND_LITERALS = {
    "--bg-panel": "#0f1519",
    "--border-dim": "#1e293b",
    "--chart-grid": "rgba(148, 163, 184, 0.16)",
    "--chart-axis": "#1e293b",
    "--chart-axis-muted": "#94a3b8",
}


def fmt_usd_exact(value: float) -> str:
    sign = "-" if value < 0 else ""
    return f"{sign}${abs(value):,.2f}"


def fmt_usd_compact(value: float) -> str:
    abs_value = abs(value)
    sign = "-" if value < 0 else ""
    if abs_value >= 1_000_000:
        return f"{sign}${abs_value / 1_000_000:.2f}M"
    return f"{sign}${abs_value:,.0f}"


def fmt_pct(value: float, digits: int = 2) -> str:
    return f"{value * 100:+.{digits}f}%"


def fmt_ratio(value: float) -> str:
    return f"{value:.2f}"


def tone_class(value: float) -> str:
    if value > 0:
        return "positive"
    if value < 0:
        return "negative"
    return "neutral"


def fmt_axis_value(value: float) -> str:
    abs_value = abs(value)
    if abs_value >= 1_000_000_000:
        return f"${value / 1_000_000_000:.1f}B"
    if abs_value >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"
    if abs_value >= 1_000:
        return f"${value / 1_000:.1f}K"
    return fmt_usd_exact(value)


def fmt_session_label(value: str) -> str:
    try:
        return datetime.fromisoformat(value).strftime("%b %d").replace(" 0", " ")
    except ValueError:
        return value


def load_payload() -> dict:
    if not DATA_PATH.exists():
        raise FileNotFoundError(f"Missing performance cache: {DATA_PATH}")
    return json.loads(DATA_PATH.read_text())


def load_chart_system() -> dict:
    if not CHART_SYSTEM_PATH.exists():
        raise FileNotFoundError(f"Missing chart-system spec: {CHART_SYSTEM_PATH}")
    return json.loads(CHART_SYSTEM_PATH.read_text())


def chart_literal(token: str) -> str:
    return BRAND_LITERALS[token]


def chart_role_color(chart_system: dict, role: str) -> str:
    return chart_system["seriesRoles"][role]["fallback"]


def chart_family_contract(chart_system: dict, family: str) -> dict:
    family_spec = chart_system["families"][family]
    renderer = family_spec["renderer"]
    return {
        "id": family,
        "label": family_spec["label"],
        "renderer": renderer,
        "interaction": family_spec["interaction"],
        "requires_axes": family_spec["requiresAxes"],
        "renderer_description": chart_system["sanctionedRenderers"][renderer],
    }


def build_chart_paths(series: List[dict], starting_equity: float) -> Dict[str, str]:
    width = 820
    height = 280
    padding_top = 24
    padding_right = 24
    padding_bottom = 28
    padding_left = 56
    equity_values = [float(point["equity"]) for point in series]
    first_benchmark = float(series[0]["benchmark_close"]) if series else 1.0
    benchmark_values = [
        (float(point["benchmark_close"]) / first_benchmark) * starting_equity
        for point in series
    ] if series else []
    all_values = equity_values + benchmark_values if benchmark_values else equity_values

    def line_path(values: List[float]) -> str:
        if not values:
            return ""
        min_value = min(all_values) if all_values else 0.0
        max_value = max(all_values) if all_values else 1.0
        span = max_value - min_value or 1.0
        lower = min_value - span * 0.1
        upper = max_value + span * 0.1
        plot_width = width - padding_left - padding_right
        plot_height = height - padding_top - padding_bottom
        parts: List[str] = []
        for index, value in enumerate(values):
            x = padding_left + (index / max(len(values) - 1, 1)) * plot_width
            y = height - padding_bottom - ((value - lower) / (upper - lower or 1.0)) * plot_height
            parts.append(f"{'M' if index == 0 else 'L'} {x:.2f} {y:.2f}")
        return " ".join(parts)

    equity_path = line_path(equity_values)
    benchmark_path = line_path(benchmark_values)
    area_path = ""
    if equity_path:
        area_path = (
            f"{equity_path} "
            f"L {width - padding_right:.2f} {height - padding_bottom:.2f} "
            f"L {padding_left:.2f} {height - padding_bottom:.2f} Z"
        )

    min_value = min(all_values) if all_values else starting_equity
    max_value = max(all_values) if all_values else starting_equity
    span = max_value - min_value or 1.0
    lower = min_value - span * 0.1
    upper = max_value + span * 0.1
    plot_height = height - padding_top - padding_bottom
    plot_width = width - padding_left - padding_right

    y_guides = []
    for index in range(4):
        tick_value = lower + ((upper - lower) / 3) * index
        y = height - padding_bottom - ((tick_value - lower) / (upper - lower or 1.0)) * plot_height
        y_guides.append(
            f'<line class="chart-guide" x1="{padding_left}" x2="{width - padding_right}" y1="{y:.2f}" y2="{y:.2f}" />'
            f'<text class="chart-label" x="{padding_left - 10}" y="{y + 3:.2f}" text-anchor="end">{html.escape(fmt_axis_value(tick_value))}</text>'
        )

    x_labels = []
    if series:
        skip = max(1, len(series) // 6)
        for index, point in enumerate(series):
            if index % skip != 0 and index != len(series) - 1:
                continue
            x = padding_left + (index / max(len(series) - 1, 1)) * plot_width
            label = fmt_session_label(str(point["date"]))
            x_labels.append(
                f'<text class="chart-label" x="{x:.2f}" y="{height - 8}" text-anchor="middle">{html.escape(label)}</text>'
            )

    return {
        "equity_path": equity_path,
        "benchmark_path": benchmark_path,
        "area_path": area_path,
        "latest_equity": fmt_usd_exact(equity_values[-1] if equity_values else starting_equity),
        "latest_benchmark_rebased": fmt_usd_exact(benchmark_values[-1] if benchmark_values else starting_equity),
        "sessions": str(len(series)),
        "y_guides_svg": "".join(y_guides),
        "x_labels_svg": "".join(x_labels),
        "plot_left": str(padding_left),
        "plot_right": str(width - padding_right),
        "plot_bottom": str(height - padding_bottom),
    }


def drawdown_trough(series: List[dict]) -> str:
    if not series:
        return "---"
    return min(series, key=lambda item: float(item["drawdown"]))["date"]


def render_rows(rows: List[dict]) -> str:
    body = []
    for row in rows:
        body.append(
            "<tr>"
            f"<td>{html.escape(row['item'])}</td>"
            f"<td>{row['display_html']}</td>"
            f"<td>{row['calc_html']}</td>"
            f"<td>{row['meaning_html']}</td>"
            "</tr>"
        )
    return "\n".join(body)


def render_section(title: str, subtitle: str, rows: List[dict]) -> str:
    return f"""
    <section class="panel">
      <div class="panel-header">
        <div>
          <div class="panel-kicker">{html.escape(title)}</div>
          <h2>{html.escape(subtitle)}</h2>
        </div>
        <div class="panel-count">{len(rows)} ITEMS</div>
      </div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Page Item</th>
              <th>Current Display</th>
              <th>How It Is Calculated</th>
              <th>What It Means</th>
            </tr>
          </thead>
          <tbody>
            {render_rows(rows)}
          </tbody>
        </table>
      </div>
    </section>
    """


def metric_row(item: str, display: str, calculation: str, meaning: str) -> dict:
    return {
        "item": item,
        "display_html": display,
        "calc_html": calculation,
        "meaning_html": meaning,
    }


def build_sections(payload: dict) -> List[str]:
    summary = payload["summary"]
    benchmark = payload["benchmark"]
    period_label = payload["period_label"]
    series = payload["series"]
    warnings = list(payload["warnings"])
    if payload["contracts_missing_history"]:
        warnings.append(
            f"{len(payload['contracts_missing_history'])} contract(s) were missing historical marks and were marked to zero where no price history was available."
        )

    chart = build_chart_paths(series, float(summary["starting_equity"]))
    warning_count = len(warnings)
    hero_rows = [
        metric_row(
            "Hero Kicker",
            f"<code>RECONSTRUCTED {html.escape(period_label)}</code>",
            "Rendered from <code>period_label</code>. The <code>RECONSTRUCTED</code> prefix is a static UI label attached to the page.",
            "Signals that the page is showing year-to-date results from a reconstructed equity curve, not a broker-native daily NAV statement series.",
        ),
        metric_row(
            "Hero Return",
            f"<span class='value {tone_class(float(summary['total_return']))}'>{fmt_pct(float(summary['total_return']))}</span>",
            (
                f"<code>(ending_equity / starting_equity) - 1</code><br>"
                f"<code>({fmt_usd_exact(float(summary['ending_equity']))} / {fmt_usd_exact(float(summary['starting_equity']))}) - 1 = {fmt_pct(float(summary['total_return']), 6)}</code>"
            ),
            "The portfolio's total year-to-date return over the reconstructed equity curve window.",
        ),
        metric_row(
            "Hero Subtitle",
            (
                f"Ending equity <code>{fmt_usd_exact(float(summary['ending_equity']))}</code> • "
                f"{html.escape(benchmark)} <code>{fmt_pct(float(payload['benchmark_total_return']))}</code> • "
                f"as of <code>{html.escape(payload['as_of'])}</code>"
            ),
            (
                "Ending equity comes from the last reconstructed curve point. "
                f"Benchmark return is <code>(benchmark_last / benchmark_first) - 1</code>. "
                f"<code>as_of</code> comes from the performance payload date."
            ),
            "Summarizes where the portfolio finished, how the benchmark performed over the same YTD window, and the effective valuation date.",
        ),
        metric_row(
            "Source Pill",
            f"<code>{'IB FLEX' if payload['trades_source'] == 'ib_flex' else 'CACHE'}</code>",
            "Displayed as <code>IB FLEX</code> when <code>trades_source == 'ib_flex'</code>; otherwise the fallback label is <code>CACHE</code>.",
            "Tells you whether the execution ledger came from live IB Flex history or a cached fallback source.",
        ),
        metric_row(
            "Days Pill",
            f"<code>{int(summary['trading_days'])} DAYS</code>",
            f"<code>summary.trading_days = len(curve.index) = {int(summary['trading_days'])}</code>",
            "The number of YTD trading sessions represented in the reconstructed curve.",
        ),
        metric_row(
            "Max DD Pill",
            f"<code>MAX DD {fmt_pct(float(summary['max_drawdown']))}</code>",
            "<code>summary.max_drawdown = min(equity / equity.cummax() - 1)</code>.",
            "A headline drawdown stress indicator. It is the worst peak-to-trough decline seen in the reconstructed YTD equity curve.",
        ),
    ]

    core_rows = [
        metric_row(
            "Core Performance Header",
            "<code>CORE PERFORMANCE</code> • <code>INSTITUTIONAL</code>",
            "Static section label and static pill.",
            "Groups the return and benchmark-relative ratios that institutional allocators and hedge funds commonly track in monthly tear sheets.",
        ),
        metric_row(
            "YTD Return Card",
            f"<strong>{fmt_pct(float(summary['total_return']))}</strong><br><span class='sub'>{fmt_usd_compact(float(summary['pnl']))} P&amp;L</span>",
            (
                f"Primary value: <code>(ending_equity / starting_equity) - 1</code>.<br>"
                f"Secondary line: <code>pnl = ending_equity - starting_equity = {fmt_usd_exact(float(summary['ending_equity']))} - {fmt_usd_exact(float(summary['starting_equity']))} = {fmt_usd_exact(float(summary['pnl']))}</code>."
            ),
            "Shows the portfolio's total YTD gain/loss in percent, with the dollar P&L directly below it.",
        ),
        metric_row(
            "Sharpe Ratio Card",
            f"<strong>{fmt_ratio(float(summary['sharpe_ratio']))}</strong><br><span class='sub'>VOL {fmt_pct(float(summary['annualized_volatility']))}</span>",
            "<code>mean(portfolio_daily_returns) / std(portfolio_daily_returns, ddof=1) * sqrt(252)</code>, with risk-free rate set to 0. The secondary line is annualized volatility.",
            "Risk-adjusted return per unit of total volatility. Higher is better because it means more return for each unit of day-to-day variability.",
        ),
        metric_row(
            "Sortino Ratio Card",
            f"<strong>{fmt_ratio(float(summary['sortino_ratio']))}</strong><br><span class='sub'>DN DEV {fmt_pct(float(summary['downside_deviation']))}</span>",
            "<code>mean(portfolio_daily_returns) / downside_rms * sqrt(252)</code>, where downside RMS is computed from only returns below 0. The secondary line is annualized downside deviation.",
            "Like Sharpe, but only penalizes harmful downside volatility instead of all volatility.",
        ),
        metric_row(
            "Max Drawdown Card",
            f"<strong>{fmt_pct(float(summary['max_drawdown']))}</strong><br><span class='sub'>{int(summary['max_drawdown_duration_days'])} DAYS</span>",
            "<code>max_drawdown = min(equity / equity.cummax() - 1)</code>. Secondary line is the longest consecutive drawdown duration measured in trading days.",
            "The worst capital loss from a prior peak, plus how long the book stayed underwater during the deepest drawdown regime.",
        ),
        metric_row(
            "Beta Card",
            f"<strong>{fmt_ratio(float(summary['beta']))}</strong><br><span class='sub'>{html.escape(benchmark)}</span>",
            f"<code>cov(portfolio_daily_returns, {benchmark}_daily_returns) / var({benchmark}_daily_returns)</code>.",
            f"Measures market sensitivity versus {benchmark}. A beta below 1 means the book has moved less than the benchmark on average.",
        ),
        metric_row(
            "Alpha Card",
            f"<strong>{fmt_pct(float(summary['alpha']))}</strong><br><span class='sub'>ANNUALIZED</span>",
            "<code>(mean(portfolio_daily_returns) - beta * mean(benchmark_daily_returns)) * 252</code>.",
            "The estimated annualized return not explained by benchmark beta alone. Positive alpha implies outperformance after accounting for benchmark exposure.",
        ),
        metric_row(
            "Information Ratio Card",
            f"<strong>{fmt_ratio(float(summary['information_ratio']))}</strong><br><span class='sub'>TE {fmt_pct(float(summary['tracking_error']))}</span>",
            "<code>mean(active_returns) / std(active_returns, ddof=1) * sqrt(252)</code>, where <code>active_returns = portfolio_returns - benchmark_returns</code>. Secondary line is annualized tracking error.",
            "Measures benchmark-relative skill: excess return earned per unit of benchmark-relative risk.",
        ),
        metric_row(
            "Calmar Ratio Card",
            f"<strong>{fmt_ratio(float(summary['calmar_ratio']))}</strong><br><span class='sub'>CUR DD {fmt_pct(float(summary['current_drawdown']))}</span>",
            "<code>annualized_return / abs(max_drawdown)</code>. Secondary line is the latest drawdown level from the current curve point.",
            "Shows how much annualized return the strategy earned relative to its worst historical drawdown. It emphasizes capital efficiency under stress.",
        ),
    ]

    chart_rows = [
        metric_row(
            "Equity Curve Header",
            f"<code>YTD EQUITY CURVE</code> • <code>{chart['sessions']} SESSIONS</code>",
            f"Static section header plus <code>len(series) = {chart['sessions']}</code>.",
            "Frames the visual section and tells you how many daily observations are plotted.",
        ),
        metric_row(
            "Legend: Portfolio",
            "<code>Portfolio</code>",
            "Uses raw <code>series[i].equity</code> values from the reconstructed YTD curve.",
            "This is the actual reconstructed portfolio equity line.",
        ),
        metric_row(
            f"Legend: {benchmark} Rebased",
            f"<code>{html.escape(benchmark)} rebased</code>",
            f"<code>(benchmark_close / first_benchmark_close) * starting_equity</code> for each day in the chart.",
            f"Normalizes {benchmark} onto the same starting dollar base as the portfolio so the two lines are visually comparable.",
        ),
        metric_row(
            "Chart Meta: Portfolio",
            f"<strong>{chart['latest_equity']}</strong>",
            "Last plotted portfolio equity value: <code>series[-1].equity</code>.",
            "The current endpoint of the reconstructed YTD portfolio curve.",
        ),
        metric_row(
            f"Chart Meta: {benchmark} Rebased",
            f"<strong>{chart['latest_benchmark_rebased']}</strong>",
            f"Last rebased benchmark value using <code>(series[-1].benchmark_close / series[0].benchmark_close) * starting_equity</code>.",
            f"The benchmark's ending value if it had started the YTD window at the same dollar level as the portfolio.",
        ),
        metric_row(
            "Chart Meta: Benchmark Return",
            f"<strong>{fmt_pct(float(payload['benchmark_total_return']))}</strong>",
            f"<code>(benchmark_last / benchmark_first) - 1</code> over the YTD window.",
            f"The benchmark's own total return, shown separately from the rebased line so you can read the percentage directly.",
        ),
    ]

    tail_rows = [
        metric_row(
            "Tail And Path Risk Header",
            "<code>TAIL AND PATH RISK</code> • <code>DAILY</code>",
            "Static section header and static timeframe pill.",
            "Groups downside and path-dependent metrics that focus on adverse daily outcomes and drawdown behavior.",
        ),
        metric_row(
            "VaR 95%",
            f"<strong>{fmt_pct(float(summary['var_95']))}</strong>",
            "<code>np.quantile(portfolio_daily_returns, 0.05)</code>.",
            "The 5th percentile daily return. It estimates the one-day loss threshold that should only be exceeded about 5% of the time.",
        ),
        metric_row(
            "CVaR 95%",
            f"<strong>{fmt_pct(float(summary['cvar_95']))}</strong>",
            "<code>mean(portfolio_daily_returns[portfolio_daily_returns &lt;= var_95])</code>.",
            "The average return on the worst 5% of days. It is a deeper tail-loss metric than VaR.",
        ),
        metric_row(
            "Tail Ratio",
            f"<strong>{fmt_ratio(float(summary['tail_ratio']))}</strong>",
            "<code>abs(q95 / var_95)</code>, where <code>q95</code> is the 95th percentile daily return.",
            "Compares upside tail size to downside tail size. Values above 1 mean the positive tail has historically been larger than the negative tail.",
        ),
        metric_row(
            "Ulcer Index",
            f"<strong>{fmt_ratio(float(summary['ulcer_index']))}</strong>",
            "<code>sqrt(mean(drawdown^2))</code> using only negative drawdown observations.",
            "Measures the depth and persistence of drawdowns. It penalizes long, painful underwater periods more than simple volatility does.",
        ),
        metric_row(
            "Worst Day",
            f"<strong>{fmt_pct(float(summary['worst_day']))}</strong>",
            "<code>min(portfolio_daily_returns)</code>.",
            "The most negative single trading-day return in the YTD sample.",
        ),
        metric_row(
            "Drawdown Trough",
            f"<strong>{html.escape(drawdown_trough(series))}</strong>",
            "<code>date_of_min(drawdown)</code> where <code>drawdown = equity / equity.cummax() - 1</code>.",
            "The calendar day when the portfolio hit its deepest drawdown point.",
        ),
    ]

    capture_rows = [
        metric_row(
            "Distribution And Capture Header",
            f"<code>DISTRIBUTION AND CAPTURE</code> • <code>{html.escape(benchmark)}</code>",
            "Static section header. The pill is the active benchmark symbol.",
            "Groups hit-rate, benchmark-participation, and return-distribution shape metrics.",
        ),
        metric_row(
            "Hit Rate",
            f"<strong>{fmt_pct(float(summary['hit_rate']))}</strong>",
            "<code>positive_days / number_of_daily_return_observations</code>.",
            "The percentage of observed trading days with a positive portfolio return.",
        ),
        metric_row(
            "Upside Capture",
            f"<strong>{fmt_ratio(float(summary['upside_capture']))}</strong>",
            "<code>portfolio_total_return_on_benchmark_up_days / benchmark_total_return_on_benchmark_up_days</code>.",
            f"Shows how much of {benchmark}'s positive-day performance the portfolio captured. Values below 1 mean the book participated less than the benchmark on up days.",
        ),
        metric_row(
            "Downside Capture",
            f"<strong>{fmt_ratio(float(summary['downside_capture']))}</strong>",
            "<code>portfolio_total_return_on_benchmark_down_days / benchmark_total_return_on_benchmark_down_days</code>.",
            f"Shows how much of {benchmark}'s negative-day performance the portfolio absorbed on down days. Lower is generally better for downside protection.",
        ),
        metric_row(
            "Correlation",
            f"<strong>{fmt_ratio(float(summary['correlation']))}</strong>",
            "<code>corrcoef(portfolio_daily_returns, benchmark_daily_returns)</code>.",
            f"Measures how tightly the portfolio's daily moves aligned with {benchmark}. Values near 1 indicate very high co-movement.",
        ),
        metric_row(
            "Skew",
            f"<strong>{fmt_ratio(float(summary['skew']))}</strong>",
            "<code>portfolio_daily_returns.skew()</code>.",
            "The asymmetry of the daily return distribution. Negative skew implies larger or more frequent downside outliers than upside outliers.",
        ),
        metric_row(
            "Kurtosis",
            f"<strong>{fmt_ratio(float(summary['kurtosis']))}</strong>",
            "<code>portfolio_daily_returns.kurt()</code>.",
            "Measures tail-heaviness relative to a normal distribution. Higher kurtosis implies fatter tails and more extreme daily returns.",
        ),
    ]

    methodology_rows = [
        metric_row(
            "Methodology Header",
            f"<code>METHODOLOGY</code> • <code>{html.escape(payload['methodology']['return_basis'].replace('_', ' ').upper())}</code>",
            "Static section header plus the payload's <code>return_basis</code> rendered as an uppercase pill.",
            "Defines the measurement convention used by the page. Here, returns are calculated from one daily close to the next.",
        ),
        metric_row(
            "Curve Type",
            f"<strong>{html.escape(payload['methodology']['curve_type'].replace('_', ' '))}</strong>",
            "<code>curve_type</code> is emitted directly by the backend payload.",
            "Tells you that the series is a reconstructed net liquidation curve built from fills plus historical marks, then anchored to current net liq.",
        ),
        metric_row(
            "Stock History",
            f"<strong>{html.escape(payload['price_sources']['stocks'])}</strong>",
            "Backend provenance string emitted in the payload.",
            "Documents the stock price-source priority used to mark stock and ETF holdings for the curve.",
        ),
        metric_row(
            "Option History",
            f"<strong>{html.escape(payload['price_sources']['options'])}</strong>",
            "Backend provenance string emitted in the payload.",
            "Documents the option mark source used to reconstruct option-leg history.",
        ),
        metric_row(
            "Risk-Free Assumption",
            f"<strong>{fmt_pct(float(payload['methodology']['risk_free_rate']))}</strong>",
            "Rendered directly from <code>methodology.risk_free_rate</code>. Current implementation uses <code>0.0</code>.",
            "Confirms the risk-free rate embedded in the Sharpe-style calculations. A zero assumption keeps the current implementation simple and explicit.",
        ),
    ]

    warning_rows = [
        metric_row(
            "Warnings Header",
            f"<code>WARNINGS</code> • <code>{warning_count} FLAGS</code>",
            f"Flag count is <code>len(warnings) + bool(contracts_missing_history)</code> = {warning_count}.",
            "Shows how many caveats or data-quality flags are attached to the current performance reconstruction.",
        ),
    ]
    for idx, warning in enumerate(warnings, start=1):
        warning_rows.append(
            metric_row(
                f"Warning {idx}",
                f"<span class='warning-copy'>{html.escape(warning)}</span>",
                "Emitted directly by the backend payload as a caveat, assumption, or data-quality note.",
                "A plain-language disclaimer that defines where the reconstruction can diverge from a broker-native audited NAV series.",
            )
        )

    return [
        render_section("Section 1", "Hero Banner", hero_rows),
        render_section("Section 2", "Core Performance", core_rows),
        render_section("Section 3", "YTD Equity Curve", chart_rows),
        render_section("Section 4", "Tail And Path Risk", tail_rows),
        render_section("Section 5", "Distribution And Capture", capture_rows),
        render_section("Section 6", "Methodology", methodology_rows),
        render_section("Section 7", "Warnings", warning_rows),
    ]


def build_html(payload: dict, chart_system: dict) -> str:
    summary = payload["summary"]
    sections = build_sections(payload)
    chart = build_chart_paths(payload["series"], float(summary["starting_equity"]))
    chart_contract = chart_family_contract(chart_system, "analytical-time-series")
    primary_color = chart_role_color(chart_system, "primary")
    comparison_color = chart_role_color(chart_system, "comparison")
    caution_color = chart_role_color(chart_system, "caution")
    fault_color = chart_role_color(chart_system, "fault")
    surface_bg = chart_literal(chart_system["surface"]["backgroundVar"])
    surface_border = chart_literal(chart_system["surface"]["borderVar"])
    chart_grid = chart_literal(chart_system["axis"]["gridVar"])
    chart_axis = chart_literal(chart_system["axis"]["axisVar"])
    chart_axis_label = chart_literal(chart_system["axis"]["labelVar"])
    chart_axis_font = chart_system["axis"]["fontFamily"]
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    total_items = sum(section.count("<tr>") for section in sections)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Performance Page Explainer</title>
  <style>
    :root {{
      --bg: #0a0f14;
      --panel: {surface_bg};
      --panel-raised: #151c22;
      --line: {surface_border};
      --text: #e2e8f0;
      --muted: #94a3b8;
      --faint: #475569;
      --series-primary: {primary_color};
      --series-comparison: {comparison_color};
      --series-caution: {caution_color};
      --fault: {fault_color};
      --chart-grid: {chart_grid};
      --chart-axis: {chart_axis};
      --chart-axis-muted: {chart_axis_label};
      --chart-radius: {int(chart_system["surface"]["radiusPx"])}px;
      --chart-padding: {int(chart_system["surface"]["paddingPx"])}px;
      --chart-axis-size: {int(chart_system["axis"]["fontSizePx"])}px;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background:
        radial-gradient(circle at top left, rgba(5, 173, 152, 0.08), transparent 28%),
        var(--bg);
      color: var(--text);
      font: 14px/1.55 Inter, ui-sans-serif, system-ui, sans-serif;
    }}
    .wrap {{
      width: min(1440px, calc(100vw - 48px));
      margin: 0 auto;
      padding: 28px 0 40px;
    }}
    .hero {{
      border: 1px solid var(--line);
      background: linear-gradient(180deg, rgba(5, 173, 152, 0.08), rgba(5, 173, 152, 0.02)), var(--panel);
      padding: 24px;
      margin-bottom: 20px;
      border-radius: var(--chart-radius);
    }}
    .eyebrow {{
      font: 600 11px/1.2 "IBM Plex Mono", ui-monospace, monospace;
      letter-spacing: .12em;
      text-transform: uppercase;
      color: var(--muted);
      margin-bottom: 8px;
    }}
    h1 {{
      margin: 0 0 8px;
      font-size: 34px;
      line-height: 1.05;
      letter-spacing: -.03em;
    }}
    .hero-sub {{
      color: var(--muted);
      max-width: 900px;
      margin-bottom: 18px;
    }}
    .hero-grid {{
      display: grid;
      grid-template-columns: 1.2fr .8fr;
      gap: 20px;
    }}
    .summary-grid {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 0;
    }}
    .summary-card {{
      border: 1px solid var(--line);
      margin-right: -1px;
      background: rgba(21, 28, 34, 0.48);
      padding: 14px;
    }}
    .summary-label {{
      font: 600 10px/1.2 "IBM Plex Mono", ui-monospace, monospace;
      letter-spacing: .12em;
      text-transform: uppercase;
      color: var(--muted);
      margin-bottom: 8px;
    }}
    .summary-value {{
      font: 500 24px/1.05 "IBM Plex Mono", ui-monospace, monospace;
      letter-spacing: -.03em;
    }}
    .positive {{ color: var(--series-primary); }}
    .negative {{ color: var(--fault); }}
    .neutral {{ color: var(--text); }}
    .hero-meta {{
      border: 1px solid var(--line);
      background: rgba(15, 21, 25, 0.78);
      padding: 14px;
    }}
    .contract-grid {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 0;
      margin-top: 16px;
    }}
    .contract-card {{
      border: 1px solid var(--line);
      margin-right: -1px;
      background: rgba(15, 21, 25, 0.62);
      padding: 14px;
    }}
    .contract-copy {{
      color: var(--muted);
      font-size: 12px;
      line-height: 1.45;
      margin-top: 6px;
    }}
    .meta-line {{
      display: flex;
      justify-content: space-between;
      gap: 16px;
      padding: 8px 0;
      border-bottom: 1px solid rgba(30, 41, 59, .65);
      font: 500 12px/1.4 "IBM Plex Mono", ui-monospace, monospace;
    }}
    .meta-line:last-child {{ border-bottom: none; }}
    .chart-panel {{
      border: 1px solid var(--line);
      background: var(--panel);
      padding: var(--chart-padding);
      margin-bottom: 20px;
      border-radius: var(--chart-radius);
    }}
    .chart-title {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 16px;
      margin-bottom: 12px;
      font: 600 11px/1.2 "IBM Plex Mono", ui-monospace, monospace;
      letter-spacing: .12em;
      text-transform: uppercase;
      color: var(--muted);
    }}
    .chart-title-meta {{
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      justify-content: flex-end;
    }}
    .chart-note {{
      margin-bottom: 12px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.5;
    }}
    .pill {{
      border: 1px solid var(--line);
      padding: 4px 8px;
      color: var(--text);
    }}
    .chart-legend {{
      display: flex;
      gap: 18px;
      margin-bottom: 10px;
      font: 500 11px/1.2 "IBM Plex Mono", ui-monospace, monospace;
      letter-spacing: .08em;
      text-transform: uppercase;
      color: var(--muted);
    }}
    .swatch {{
      display: inline-block;
      width: 12px;
      height: 12px;
      vertical-align: -2px;
      margin-right: 6px;
      border: 1px solid var(--line);
    }}
    .swatch.portfolio {{ background: var(--series-primary); }}
    .swatch.benchmark {{ background: var(--series-comparison); }}
    svg {{
      width: 100%;
      height: auto;
      display: block;
      border: 1px solid var(--line);
      background: linear-gradient(180deg, rgba(15,21,25,.84), rgba(10,15,20,.95));
      border-radius: var(--chart-radius);
    }}
    .panel {{
      border: 1px solid var(--line);
      background: var(--panel);
      margin-bottom: 20px;
      border-radius: var(--chart-radius);
    }}
    .panel-header {{
      display: flex;
      justify-content: space-between;
      gap: 16px;
      padding: 16px 18px;
      border-bottom: 1px solid var(--line);
      align-items: end;
    }}
    .panel-kicker {{
      font: 600 10px/1.2 "IBM Plex Mono", ui-monospace, monospace;
      letter-spacing: .12em;
      text-transform: uppercase;
      color: var(--muted);
      margin-bottom: 6px;
    }}
    h2 {{
      margin: 0;
      font-size: 20px;
      letter-spacing: -.02em;
    }}
    .panel-count {{
      font: 500 11px/1.2 "IBM Plex Mono", ui-monospace, monospace;
      letter-spacing: .12em;
      text-transform: uppercase;
      color: var(--muted);
    }}
    .table-wrap {{
      overflow-x: auto;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
    }}
    th, td {{
      border-bottom: 1px solid var(--line);
      vertical-align: top;
      padding: 14px 16px;
    }}
    th {{
      text-align: left;
      font: 600 10px/1.2 "IBM Plex Mono", ui-monospace, monospace;
      letter-spacing: .12em;
      text-transform: uppercase;
      color: var(--muted);
      background: rgba(21, 28, 34, 0.5);
      position: sticky;
      top: 0;
    }}
    td:first-child {{
      font: 500 12px/1.35 "IBM Plex Mono", ui-monospace, monospace;
      letter-spacing: .04em;
      text-transform: uppercase;
      width: 18%;
    }}
    td:nth-child(2) {{ width: 22%; }}
    td:nth-child(3) {{ width: 30%; }}
    td:nth-child(4) {{ width: 30%; }}
    code, .sub, .warning-copy {{
      font-family: "IBM Plex Mono", ui-monospace, monospace;
    }}
    .sub {{
      color: var(--muted);
      font-size: 11px;
      letter-spacing: .06em;
      text-transform: uppercase;
    }}
    .foot {{
      padding: 18px;
      border: 1px solid var(--line);
      background: var(--panel);
      color: var(--muted);
      font: 500 12px/1.6 "IBM Plex Mono", ui-monospace, monospace;
      letter-spacing: .03em;
    }}
    .sources {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 12px;
      margin-top: 14px;
    }}
    .source-card {{
      border: 1px solid var(--line);
      padding: 12px;
      background: rgba(21, 28, 34, 0.45);
      border-radius: var(--chart-radius);
    }}
    @media (max-width: 1100px) {{
      .hero-grid, .summary-grid, .sources, .contract-grid {{
        grid-template-columns: 1fr;
      }}
    }}
    @media (max-width: 760px) {{
      .wrap {{
        width: min(100vw - 24px, 100%);
        padding-top: 12px;
      }}
      th, td {{
        padding: 12px;
      }}
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <div class="eyebrow">Radon Terminal /performance Explainer</div>
      <h1>Every Rendered Item On The Performance Page</h1>
      <div class="hero-sub">
        This report maps each visible item on <code>/performance</code> to its current value, its exact calculation logic, and its institutional meaning. Values are taken from <code>data/performance.json</code>, which is the cache served by <code>/api/performance</code>.
      </div>
      <div class="hero-grid">
        <div class="summary-grid">
          <div class="summary-card">
            <div class="summary-label">As Of</div>
            <div class="summary-value neutral">{html.escape(payload['as_of'])}</div>
          </div>
          <div class="summary-card">
            <div class="summary-label">YTD Return</div>
            <div class="summary-value {tone_class(float(summary['total_return']))}">{fmt_pct(float(summary['total_return']))}</div>
          </div>
          <div class="summary-card">
            <div class="summary-label">Ending Equity</div>
            <div class="summary-value neutral">{fmt_usd_exact(float(summary['ending_equity']))}</div>
          </div>
          <div class="summary-card">
            <div class="summary-label">Rendered Items</div>
            <div class="summary-value neutral">{total_items}</div>
          </div>
        </div>
        <div class="hero-meta">
          <div class="meta-line"><span>Period</span><strong>{html.escape(payload['period_label'])}</strong></div>
          <div class="meta-line"><span>Benchmark</span><strong>{html.escape(payload['benchmark'])}</strong></div>
          <div class="meta-line"><span>Trade Source</span><strong>{html.escape(payload['trades_source'])}</strong></div>
          <div class="meta-line"><span>Last Sync</span><strong>{html.escape(payload['last_sync'])}</strong></div>
          <div class="meta-line"><span>Generated</span><strong>{html.escape(generated_at)}</strong></div>
        </div>
      </div>
      <div class="contract-grid">
        <div class="contract-card">
          <div class="summary-label">Chart Family</div>
          <div class="summary-value neutral">{html.escape(chart_contract['label'])}</div>
          <div class="contract-copy">This surface is sanctioned as an <code>{html.escape(chart_contract['id'])}</code> chart.</div>
        </div>
        <div class="contract-card">
          <div class="summary-label">Renderer</div>
          <div class="summary-value neutral">{html.escape(chart_contract['renderer']).upper()}</div>
          <div class="contract-copy">{html.escape(chart_contract['renderer_description'])}</div>
        </div>
        <div class="contract-card">
          <div class="summary-label">Axis Contract</div>
          <div class="summary-value neutral">{'REQUIRED' if chart_contract['requires_axes'] else 'OPTIONAL'}</div>
          <div class="contract-copy">Axes use <code>{html.escape(chart_system['axis']['fontFamily'])}</code> at <code>{chart_system['axis']['fontSizePx']}px</code>.</div>
        </div>
        <div class="contract-card">
          <div class="summary-label">Semantic Roles</div>
          <div class="summary-value neutral">PRIMARY / COMPARISON</div>
          <div class="contract-copy">Portfolio owns the thesis line; the benchmark is a rebased comparison overlay.</div>
        </div>
      </div>
      <div class="sources">
        <div class="source-card"><strong>Metric Engine</strong><br><code>scripts/portfolio_performance.py</code></div>
        <div class="source-card"><strong>Rendered Surface</strong><br><code>web/components/PerformancePanel.tsx</code></div>
        <div class="source-card"><strong>Chart System</strong><br><code>web/lib/chart-system-spec.json</code></div>
        <div class="source-card"><strong>Conventions</strong><br><code>empyrical</code> / <code>quantstats</code> aligned, risk-free = 0</div>
      </div>
    </section>

    <section class="chart-panel">
      <div class="chart-title">
        <span>Current YTD Equity Curve Visual Context</span>
        <div class="chart-title-meta">
          <span class="pill">{html.escape(chart_contract['label']).upper()}</span>
          <span class="pill">{html.escape(chart_contract['renderer']).upper()}</span>
          <span class="pill">{chart['sessions']} SESSIONS</span>
        </div>
      </div>
      <div class="chart-note">
        This panel uses the shared chart-system contract: <code>primary</code> for portfolio, <code>comparison</code> for the rebased benchmark, mono axis labels, and an explicit analytical time-series frame.
      </div>
      <div class="chart-legend">
        <span><span class="swatch portfolio"></span>Portfolio / Primary</span>
        <span><span class="swatch benchmark"></span>{html.escape(payload['benchmark'])} rebased / Comparison</span>
      </div>
      <svg viewBox="0 0 820 280" role="img" aria-label="Current YTD portfolio vs rebased benchmark">
        <defs>
          <linearGradient id="explainerAreaGradient" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stop-color="{primary_color}" stop-opacity="0.22" />
            <stop offset="100%" stop-color="{primary_color}" stop-opacity="0.02" />
          </linearGradient>
        </defs>
        <style>
          .chart-guide {{
            stroke: {chart_grid};
            stroke-width: 1;
          }}
          .chart-axis {{
            stroke: {chart_axis};
            stroke-width: 1;
          }}
          .chart-label {{
            fill: {chart_axis_label};
            font-family: {chart_axis_font};
            font-size: {int(chart_system["axis"]["fontSizePx"])}px;
            letter-spacing: {chart_system["axis"]["trackingEm"]}em;
          }}
        </style>
        {chart['y_guides_svg']}
        <line x1="{chart['plot_left']}" x2="{chart['plot_right']}" y1="{chart['plot_bottom']}" y2="{chart['plot_bottom']}" class="chart-axis" />
        <path d="{chart['area_path']}" fill="url(#explainerAreaGradient)"></path>
        <path d="{chart['benchmark_path']}" fill="none" stroke="{comparison_color}" stroke-width="2" stroke-dasharray="6 5"></path>
        <path d="{chart['equity_path']}" fill="none" stroke="{primary_color}" stroke-width="2"></path>
        {chart['x_labels_svg']}
      </svg>
    </section>

    {''.join(sections)}

    <section class="foot">
      <div><strong>Definition scope:</strong> this report covers every current, visible item on <code>/performance</code>, including headers, pills, chart labels, chart meta values, methodology provenance, and warnings.</div>
      <div><strong>Assumption carried through from the backend:</strong> the reconstructed curve is anchored to current net liquidation and assumes no unmodeled external cash flows inside the observed YTD window.</div>
    </section>
  </div>
</body>
</html>
"""


def write_report(output_path: Path) -> Path:
    payload = load_payload()
    chart_system = load_chart_system()
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    html_content = build_html(payload, chart_system)
    output_path.write_text(html_content)
    return output_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate an HTML explainer for the /performance page")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Output HTML path")
    parser.add_argument("--no-open", action="store_true", help="Do not open the report in the browser")
    args = parser.parse_args()

    out = write_report(args.output.resolve())
    print(out)
    if not args.no_open:
        webbrowser.open(f"file://{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
