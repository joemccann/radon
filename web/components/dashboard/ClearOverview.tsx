"use client";

import { memo, useMemo, useState, type KeyboardEvent } from "react";
import Link from "next/link";
import { ArrowDownRight, ArrowRight, ArrowUpRight, CircleAlert, ShieldCheck } from "lucide-react";
import { CLEAR_PERIODS, buildClearHistory, deriveClearAccount, deriveClearExposure, selectClearHistory, type ClearPeriod } from "@/lib/clearOverview";
import { buildPerformanceChartModel } from "@/lib/performanceChart";
import { getPnlDollars, resolveRealtimeMarketValue } from "@/lib/positionUtils";
import { usePerformance } from "@/lib/usePerformance";
import type { PriceData } from "@/lib/pricesProtocol";
import type { PerformanceData, PortfolioData } from "@/lib/types";
import styles from "./ClearOverview.module.css";

const EMPTY_PRICES: Record<string, PriceData> = {};
const usd = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });
const accountUsd = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: 2 });
const dateLabel = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });

function money(value: number | null): string {
  return value == null || !Number.isFinite(value) ? "---" : usd.format(value);
}

function signedMoney(value: number | null): string {
  return value == null ? "---" : `${value > 0 ? "+" : value < 0 ? "−" : ""}${usd.format(Math.abs(value))}`;
}

function formatDate(value: string): string {
  const date = new Date(value.length === 10 ? `${value}T00:00:00Z` : value);
  return Number.isNaN(date.getTime()) ? "Date unavailable" : dateLabel.format(date);
}

/** Isolated from the quote stream: history fetches once, then every 15 minutes. */
const ClearAccountHistory = memo(function ClearAccountHistory() {
  const { data, loading, error } = usePerformance(true);
  return <AccountHistory data={data} loading={loading} error={error} />;
});

export function AccountHistory({ data, loading = false, error = null }: { data: PerformanceData | null; loading?: boolean; error?: string | null }) {
  const history = useMemo(() => buildClearHistory(data), [data]);
  const [period, setPeriod] = useState<ClearPeriod>("ALL");
  const selectedPeriod = history.availablePeriods.includes(period) ? period : "ALL";
  const points = useMemo(() => selectClearHistory(history, selectedPeriod), [history, selectedPeriod]);
  const [inspection, setInspection] = useState<number | null>(null);
  const inspectedIndex = inspection == null ? null : Math.min(inspection, Math.max(0, points.length - 1));
  const inspected = inspectedIndex == null ? null : points[inspectedIndex];
  const model = useMemo(() => {
    if (!data || points.length < 2) return null;
    return buildPerformanceChartModel({
      ...data,
      series: points.map((point) => ({ date: point.date, equity: point.value, daily_return: null, drawdown: 0 })),
    }, 820, 230, { top: 14, right: 10, bottom: 14, left: 10 });
  }, [data, points]);
  const indexX = inspectedIndex == null || !model ? 0 : model.plotLeft + inspectedIndex / Math.max(1, points.length - 1) * (model.plotRight - model.plotLeft);
  const stale = history.status === "stale" || history.status === "degraded";

  function inspectByKeyboard(event: KeyboardEvent<HTMLDivElement>) {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const current = inspection ?? points.length - 1;
    setInspection(event.key === "Home" ? 0 : event.key === "End" ? points.length - 1 : Math.max(0, Math.min(points.length - 1, current + (event.key === "ArrowRight" ? 1 : -1))));
  }

  return (
    <section className={styles.history} aria-label="Account value history" aria-busy={loading}>
      <div className={styles.chartLegend}>
        <span><i aria-hidden="true" />Account value <span className={styles.secondary}>· USD</span></span>
        <span className={stale ? styles.attentionText : styles.secondary}>
          {inspected ? `${formatDate(inspected.date)} · ${money(inspected.value)}` : history.asOf ? `${stale ? "Dated snapshot · " : ""}${formatDate(history.asOf)}` : "Daily closing values"}
        </span>
      </div>
      {model ? (
        <div
          className={styles.chart}
          role="slider"
          tabIndex={0}
          aria-label="Inspect account value history"
          aria-valuemin={0}
          aria-valuemax={points.length - 1}
          aria-valuenow={inspectedIndex ?? points.length - 1}
          aria-valuetext={`${formatDate((inspected ?? points.at(-1)!).date)}, ${money((inspected ?? points.at(-1)!).value)}`}
          onKeyDown={inspectByKeyboard}
          onPointerMove={(event) => {
            const rect = event.currentTarget.getBoundingClientRect();
            const fraction = (event.clientX - rect.left) / rect.width;
            setInspection(Math.max(0, Math.min(points.length - 1, Math.round(fraction * (points.length - 1)))));
          }}
          onPointerLeave={() => setInspection(null)}
          onBlur={() => setInspection(null)}
        >
          <svg viewBox="0 0 820 230" preserveAspectRatio="none" aria-hidden="true">
            {model.yAxisTicks.map((tick) => <line key={tick.value} x1={model.plotLeft} x2={model.plotRight} y1={tick.y} y2={tick.y} className={styles.chartGrid} />)}
            <path d={model.areaPath} className={styles.chartArea} />
            <path d={model.equityPath} className={styles.chartLine} />
            {inspectedIndex != null ? <line x1={indexX} x2={indexX} y1={14} y2={216} className={styles.chartCursor} /> : null}
          </svg>
        </div>
      ) : (
        <div className={styles.historyEmpty}>
          <strong>{loading ? "Loading account history" : "Account history unavailable"}</strong>
          <p>{loading ? "Retrieving daily closing values." : error ? "The account history source could not be reached. Your current account snapshot remains above." : "At least two verified dollar NAV observations are needed to draw this chart."}</p>
          {!loading ? <Link href="/performance" className={styles.textLink}>View performance details <ArrowRight size={15} aria-hidden="true" /></Link> : null}
        </div>
      )}
      {model ? <div className={styles.chartDates}><span>{formatDate(points[0].date)}</span><span>{formatDate(points.at(-1)!.date)}</span></div> : null}
      <div className={styles.chartControls}>
        <div className={styles.periods} aria-label="Account history period">
          {CLEAR_PERIODS.map((option) => (
            <button key={option} type="button" aria-pressed={option === selectedPeriod} disabled={!history.availablePeriods.includes(option)} onClick={() => { setPeriod(option); setInspection(null); }}>{option === "ALL" ? "All" : option}</button>
          ))}
        </div>
        <Link href="/performance" className={styles.textLink}>Performance <ArrowUpRight size={14} aria-hidden="true" /></Link>
      </div>
      <p className={styles.chartNote}>Daily NAV includes deposits and withdrawals. Cash-flow-adjusted returns are in Performance.</p>
    </section>
  );
}

export default function ClearOverview({ portfolio, prices = EMPTY_PRICES }: { portfolio: PortfolioData | null; prices?: Record<string, PriceData> }) {
  const account = deriveClearAccount(portfolio);
  const exposure = useMemo(() => deriveClearExposure(portfolio, prices), [portfolio, prices]);
  const positions = portfolio?.positions ?? [];
  const margin = account.margin;
  const needsAttention = margin.level !== "none";
  const balance = account.value == null ? "---" : accountUsd.format(account.value);
  const [wholeBalance, decimals] = balance.split(".");
  const riskTitle = margin.degraded ? "Margin data unavailable" : needsAttention ? "Margin needs attention" : "Margin cushion";

  return (
    <div className={styles.overview} data-testid="clear-overview">
      <div className={styles.main}>
        <section className={styles.account} aria-label="Your account">
          <div className={styles.accountLabel}>Total account value <span>Net liquidation</span></div>
          <div className={styles.balance} data-testid="clear-account-value">{wholeBalance}{decimals ? <span>.{decimals}</span> : null}</div>
          <div className={styles.dayPnl} data-tone={account.dailyPnl == null || account.dailyPnl === 0 ? "neutral" : account.dailyPnl > 0 ? "positive" : "negative"}>
            {account.dailyPnl != null ? <>{account.dailyPnl < 0 ? <ArrowDownRight size={17} aria-hidden="true" /> : <ArrowUpRight size={17} aria-hidden="true" />}<strong>{signedMoney(account.dailyPnl)}</strong><span>Today&apos;s P&amp;L</span></> : <span>Today&apos;s P&amp;L is unavailable for this snapshot.</span>}
          </div>
        </section>
        <ClearAccountHistory />
        <dl className={styles.metrics}>
          <div><dt>Buying power</dt><dd>{money(account.buyingPower)}</dd></div>
          <div><dt>Margin used</dt><dd>{account.marginUsedPct == null ? "---" : `${account.marginUsedPct.toFixed(1)}%`}</dd></div>
          <div><dt>Net dollar delta</dt><dd title={exposure.complete ? "Sum of position delta × underlying price" : "Complete underlying prices and provider option deltas required"}>{signedMoney(exposure.dollarDelta)}</dd></div>
        </dl>
        <Link className={styles.mobileRisk} data-tone={margin.level === "critical" ? "critical" : needsAttention ? "attention" : "neutral"} href="#clear-risk-details">
          {needsAttention || margin.degraded ? <CircleAlert size={20} aria-hidden="true" /> : <ShieldCheck size={20} aria-hidden="true" />}
          <span><strong>{riskTitle}</strong><small>{margin.cushionPct == null ? "Review available account risk data" : `${margin.cushionPct.toFixed(1)}% excess liquidity / net liquidation`}</small></span>
          <ArrowRight size={17} aria-hidden="true" />
        </Link>
        <section className={styles.positions} aria-labelledby="clear-positions-title">
          <div className={styles.sectionHeading}><h2 id="clear-positions-title">Your positions <span>{positions.length}</span></h2><Link href="/portfolio" className={styles.textLink}>View all <ArrowRight size={16} aria-hidden="true" /></Link></div>
          {positions.length ? (
            <>
              <div className={styles.positionHead} aria-hidden="true"><span>Position</span><span>Structure</span><span>Total P&amp;L</span></div>
              <ul className={styles.positionList}>
                {positions.slice(0, 4).map((position) => {
                  const pnl = getPnlDollars(position, resolveRealtimeMarketValue(position, prices) ?? undefined);
                  return (
                    <li key={position.id}>
                      <Link href={`/${encodeURIComponent(position.ticker.toUpperCase())}?posId=${position.id}`} prefetch={false} className={styles.position}>
                        <span className={styles.security}><span className={styles.securityMark} aria-hidden="true">{position.ticker.slice(0, 1)}</span><span><strong>{position.ticker}</strong><small>{position.contracts.toLocaleString("en-US")} {position.structure_type === "Stock" ? "shares" : "contracts"}</small></span></span>
                        <span className={styles.structure}>{position.structure}<small>{position.expiry && position.expiry !== "N/A" ? formatDate(position.expiry) : "Equity"}</small></span>
                        <span className={styles.positionPnl} data-tone={pnl == null || pnl === 0 ? "neutral" : pnl > 0 ? "positive" : "negative"}>{signedMoney(pnl)}<small>Total P&amp;L</small></span>
                      </Link>
                    </li>
                  );
                })}
              </ul>
            </>
          ) : <div className={styles.positionEmpty}><strong>{portfolio ? "No open positions" : "Waiting for your portfolio"}</strong><p>{portfolio ? "Research an instrument, review its structure, and size it against your account." : "Positions will appear when an account snapshot is available."}</p><Link href={portfolio ? "/scanner" : "/portfolio"} className={styles.textLink}>{portfolio ? "Explore research" : "View connection details"} <ArrowRight size={15} aria-hidden="true" /></Link></div>}
          <p className={styles.sourceNote}>{portfolio?.last_sync ? `Account snapshot · ${formatDate(portfolio.last_sync)}` : "Account snapshot not yet available"}</p>
        </section>
      </div>
      <aside className={styles.rail} aria-label="Risk and research">
        <section id="clear-risk-details" className={styles.risk} aria-labelledby="clear-risk-title">
          <div className={styles.sectionHeading}><h2 id="clear-risk-title">Risk at a glance</h2><ShieldCheck size={20} aria-hidden="true" /></div>
          <p className={margin.level === "critical" ? styles.criticalText : needsAttention ? styles.attentionText : styles.secondary}>{riskTitle}</p>
          <div className={styles.riskValue}><span>Available cushion</span><strong>{margin.cushionPct == null ? "---" : <>{margin.cushionPct.toFixed(1)}<small>%</small></>}</strong></div>
          {margin.cushionPct != null ? <div className={styles.riskTrack} data-tone={margin.level === "critical" ? "critical" : needsAttention ? "attention" : "neutral"}><span style={{ width: `${Math.max(0, Math.min(100, margin.cushionPct))}%` }} /></div> : null}
          <p className={styles.riskDescription}>{margin.degraded ? "Current net liquidation and excess liquidity are needed to assess margin." : needsAttention ? margin.message : "Excess liquidity / net liquidation. Margin cushion is not a maximum-loss limit."}</p>
          <Link href="/portfolio" className={styles.textLink}>Review positions <ArrowRight size={16} aria-hidden="true" /></Link>
        </section>
        <section className={styles.exposure} aria-labelledby="clear-exposure-title">
          <h3 id="clear-exposure-title">Exposure</h3>
          <dl><div><dt>Net dollar delta</dt><dd>{signedMoney(exposure.dollarDelta)}</dd></div><div><dt>Undefined-risk positions</dt><dd>{portfolio ? portfolio.undefined_risk_count : "---"}</dd></div></dl>
          <p>{exposure.complete ? "Sensitivity to underlying prices, not a repriced stress scenario." : "Current underlying prices and provider Greeks are required for every option leg."}</p>
          <Link href="/regime/cri" className={styles.textLink}>View market risk <ArrowUpRight size={16} aria-hidden="true" /></Link>
        </section>
        <section className={styles.research} aria-labelledby="clear-research-title">
          <span className={styles.eyebrow}>Research workspace</span>
          <h3 id="clear-research-title">Follow the evidence.</h3>
          <p>Explore ranked structures, institutional flow, and the catalysts behind your next decision.</p>
          <Link href="/scanner" className={styles.primary}>Explore research <ArrowRight size={17} aria-hidden="true" /></Link>
          <Link href="/watchlist" className={styles.secondaryAction}>Open your watchlist</Link>
          <a href="#clear-market-intelligence" className={styles.textLink}>News, signals &amp; catalysts <ArrowRight size={15} aria-hidden="true" /></a>
        </section>
      </aside>
    </div>
  );
}
