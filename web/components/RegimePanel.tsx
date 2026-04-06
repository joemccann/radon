"use client";

import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, Check, Shield, X, Zap } from "lucide-react";
import CriHistoryChart from "./CriHistoryChart";
import RegimeRelationshipView from "./RegimeRelationshipView";
import VcgPanel from "./VcgPanel";
import GexPanel from "./GexPanel";
import { DayChange, LiveBadge, PointChange, RegimeStrip, RegimeStripCell } from "./RegimeStrip";
import ShareReportModal from "./ShareReportModal";
import type { ChartSeries, CriHistoryEntry } from "./CriHistoryChart";
import InfoTooltip from "./InfoTooltip";
import type { PriceData } from "@/lib/pricesProtocol";
import { chartSeriesColor } from "@/lib/chartSystem";
import { resolveRegimeStripLiveState } from "@/lib/regimeLiveStrip";
import { useRegime } from "@/lib/useRegime";
import { SECTION_TOOLTIPS } from "@/lib/sectionTooltips";
import { computeCri, type CriLevel, type CriResult } from "@/lib/criCalc";
import { MarketState } from "@/lib/useMarketHours";

type RegimeTab = "cri" | "vcg" | "gex";

type RegimePanelProps = {
  prices: Record<string, PriceData>;
  dataEndpoint?: string;
  shareEndpoint?: string;
  shareContentEndpoint?: string;
  shareModalTitle?: string;
  shareButtonTitle?: string;
  shareContentTitle?: string;
  marketState?: MarketState;
};

/* ─── Helpers ────────────────────────────────────────── */

function levelColor(level: CriLevel): string {
  switch (level) {
    case "LOW": return "var(--positive)";
    case "ELEVATED": return "var(--warning)";
    case "HIGH": return "var(--negative)";
    case "CRITICAL": return "var(--negative)";
  }
}

function fmt(v: number | null | undefined, decimals = 2): string {
  if (v == null || !Number.isFinite(v)) return "---";
  return v.toFixed(decimals);
}

function fmtPct(v: number | null | undefined, decimals = 2): string {
  if (v == null || !Number.isFinite(v)) return "---";
  return `${v >= 0 ? "+" : ""}${v.toFixed(decimals)}%`;
}

function fmtSigned(v: number | null | undefined, decimals = 2): string {
  if (v == null || !Number.isFinite(v)) return "---";
  return `${v >= 0 ? "+" : ""}${v.toFixed(decimals)}`;
}

/* ─── Component Bar ──────────────────────────────────── */

const COMPONENT_TOOLTIPS: Record<string, string> = {
  VIX: "CBOE Volatility Index — 30-day implied vol of SPX. Score rises as VIX exceeds 20 (elevated) and 30 (high). Extreme spikes indicate tail-risk hedging by institutional players.",
  VVIX: "Vol-of-VIX — measures expected volatility of VIX itself. Score rises with absolute level and VVIX/VIX ratio >5, signalling second-order stress and unstable vol regimes.",
  CORRELATION: "Cboe 1-Month Implied Correlation Index (COR1M). High COR1M (>60) means the market expects the largest S&P 500 stocks to move together, reducing diversification and signalling herding risk.",
  MOMENTUM: "SPX distance below 100-day MA combined with VIX 5-day rate of change. Captures both price trend stress and velocity of volatility acceleration.",
};

function ComponentBar({ label, score, live }: { label: string; score: number; live: boolean }) {
  const pct = (score / 25) * 100;
  const barColor = score < 8 ? "var(--positive)" : score > 16 ? "var(--negative)" : "var(--warning)";
  const tooltip = COMPONENT_TOOLTIPS[label];
  return (
    <div className="regime-component-bar">
      <div className="regime-component-label">
        <span style={{ flex: 1 }}>{label}</span>
        {tooltip && <InfoTooltip text={tooltip} />}
        <LiveBadge live={live} />
      </div>
      <div className="regime-bar-track">
        <div className="regime-bar-fill" style={{ width: `${pct}%`, background: barColor }} />
      </div>
      <div className="regime-component-score">{score.toFixed(1)}/25</div>
    </div>
  );
}

/* ─── Trigger Row ────────────────────────────────────── */

function TriggerRow({ label, met, value, live }: { label: string; met: boolean; value: string; live: boolean }) {
  return (
    <div className="regime-trigger-row">
      <div className="regime-trigger-icon">
        {met ? <Check size={14} color="var(--positive)" /> : <X size={14} color="var(--negative)" />}
      </div>
      <div className="regime-trigger-label">{label}</div>
      <div className="regime-trigger-value">{value}</div>
      <LiveBadge live={live} />
    </div>
  );
}

/* ─── Main Panel ─────────────────────────────────────── */

export default function RegimePanel({
  prices,
  dataEndpoint,
  shareEndpoint,
  shareContentEndpoint,
  shareModalTitle = "REGIME REPORT — SHARE TO X",
  shareButtonTitle = "Share Regime report to X",
  shareContentTitle = "Regime Share Preview",
  marketState,
}: RegimePanelProps) {
  const [activeTab, setActiveTab] = useState<RegimeTab>("cri");
  const { data, syncing, lastSync } = useRegime(marketState, { endpoint: dataEndpoint });
  const shareModal = shareEndpoint ? (
    <ShareReportModal
      modalTitle={shareModalTitle}
      shareEndpoint={shareEndpoint}
      contentEndpoint={shareContentEndpoint}
      buttonTitle={shareButtonTitle}
      iconSize={11}
      shareContentTitle={shareContentTitle}
    />
  ) : (
    <ShareReportModal
      modalTitle={shareModalTitle}
      shareEndpoint="/api/regime/share"
      contentEndpoint={shareContentEndpoint}
      buttonTitle={shareButtonTitle}
      iconSize={11}
      shareContentTitle={shareContentTitle}
    />
  );

  // market_open flag from CRI data — controls session-close display and
  // whether intraday realized-vol is computed from live SPY ticks.
  // Default true (live) until the first CRI payload arrives.
  const marketOpen = data?.market_open ?? true;

  const stripState = useMemo(
    () => resolveRegimeStripLiveState({ prices, data }),
    [prices, data],
  );
  const {
    liveVix,
    liveVvix,
    liveSpy,
    liveCor1m,
    hasLiveVix,
    hasLiveVvix,
    hasLiveSpy,
    hasLiveCor1m,
    vixValue: vixVal,
    vvixValue: vvixVal,
    spyValue: spyVal,
    cor1mValue: activeCorr,
    vixClose,
    vvixClose,
    spyClose,
    cor1mPreviousClose,
    corr5dChange,
    vvixVixRatio,
    spxDistancePct: spxDistPct,
  } = stripState;
  const hasLive = hasLiveVix || hasLiveVvix || hasLiveSpy || hasLiveCor1m;

  // When market is closed, IB still reports Friday's close as "last" for
  // indices (VIX, VVIX).  The WS relay pushes those stale values, making
  // hasLiveVix etc. true even on weekends.  Gate all display-level "live"
  // indicators on the actual market state so badges read DAILY, not LIVE.
  const effectiveHasLiveVix = marketOpen && hasLiveVix;
  const effectiveHasLiveVvix = marketOpen && hasLiveVvix;
  const effectiveHasLiveSpy = marketOpen && hasLiveSpy;
  const effectiveHasLiveCor1m = marketOpen && hasLiveCor1m;
  const effectiveHasLive = marketOpen && hasLive;

  // Timestamps for last live VIX / VVIX value received
  const [vixLastTs, setVixLastTs] = useState<string | null>(null);
  const [vvixLastTs, setVvixLastTs] = useState<string | null>(null);

  useEffect(() => {
    if (liveVix == null) {
      setVixLastTs(null);
      return;
    }
    setVixLastTs(new Date().toLocaleTimeString());
  }, [liveVix]);

  useEffect(() => {
    if (liveVvix == null) {
      setVvixLastTs(null);
      return;
    }
    setVvixLastTs(new Date().toLocaleTimeString());
  }, [liveVvix]);

  // ── Intraday realized vol ────────────────────────────────────────────────
  // Replace today's last close in the 21-day SPY series with the live price,
  // then recompute: std(log_returns) * sqrt(252) * 100  (same as cri_scan.py).
  // When the market is closed, skip live computation — use data?.realized_vol.
  const intradayRvol = useMemo(() => {
    if (!marketOpen) return null;
    if (liveSpy == null || !data?.spy_closes?.length) return null;
    const closes = data.spy_closes;
    // Need at least 21 prices to get 20 log-returns.
    if (closes.length < 21) return null;
    // Replace the last historical close with the live price.
    const series = [...closes.slice(0, -1), liveSpy];
    const n = series.length;
    const logReturns: number[] = [];
    for (let i = 1; i < n; i++) {
      if (series[i - 1] > 0) logReturns.push(Math.log(series[i] / series[i - 1]));
    }
    if (logReturns.length < 2) return null;
    const mean = logReturns.reduce((s, r) => s + r, 0) / logReturns.length;
    const variance = logReturns.reduce((s, r) => s + (r - mean) ** 2, 0) / (logReturns.length - 1);
    return Math.sqrt(variance) * Math.sqrt(252) * 100;
  }, [marketOpen, liveSpy, data?.spy_closes]);

  const hasIntradayRvol = intradayRvol != null;
  const activeRvol = intradayRvol ?? data?.realized_vol ?? null;
  const nqSkew = data?.nq_skew ?? data?.nasdaq_skew ?? null;
  const formattedNqSkew = fmtSigned(nqSkew, 3);

  const activeCorrChange = corr5dChange ?? 0;
  const safeActiveCorr = activeCorr ?? 0;
  const correlationTriggerMet =
    data?.crash_trigger?.conditions.cor1m_gt_60 ?? safeActiveCorr > 60;

  // Merge live + cached into CRI inputs. If any live values are present,
  // recompute CRI from them to avoid waiting for the scan loop.
  const liveCri: CriResult | null = useMemo(() => {
    if (!data) return null;
    if (!effectiveHasLive) return null;

    const vix = liveVix ?? data.vix;
    const vvix = liveVvix ?? data.vvix;
    const spy = liveSpy ?? data.spy;
    const vvixVixRatio = vix > 0 ? vvix / vix : data.vvix_vix_ratio ?? 0;
    const ma = data.spx_100d_ma;
    const spxDistancePct = ma && ma > 0 ? ((spy / ma) - 1) * 100 : data.spx_distance_pct;

    return computeCri({
      vix,
      vix5dRoc: data.vix_5d_roc,
      vvix,
      vvixVixRatio,
      corr: safeActiveCorr,
      corr5dChange: activeCorrChange,
      spxDistancePct,
    });
  }, [data, effectiveHasLive, liveVix, liveVvix, liveSpy, activeCorrChange, safeActiveCorr]);

  const cri = liveCri ?? (data?.cri ? { ...data.cri, level: data.cri.level as CriLevel } : { score: 0, level: "LOW" as CriLevel, components: { vix: 0, vvix: 0, correlation: 0, momentum: 0 } });
  const color = levelColor(cri.level);
  const ma = data?.spx_100d_ma;
  const spxBelowMa = ma && spyVal != null
    ? spyVal < ma
    : data?.crash_trigger?.conditions.spx_below_100d_ma ?? false;

  const tabBar = (
    <div className="ticker-tabs" style={{ marginBottom: "16px" }}>
      <button className={`ticker-tab ${activeTab === "cri" ? "active" : ""}`} onClick={() => setActiveTab("cri")}>CRI</button>
      <button className={`ticker-tab ${activeTab === "vcg" ? "active" : ""}`} onClick={() => setActiveTab("vcg")}>VCG</button>
      <button className={`ticker-tab ${activeTab === "gex" ? "active" : ""}`} onClick={() => setActiveTab("gex")}>GEX</button>
    </div>
  );

  if (activeTab === "vcg") {
    return (
      <div className="regime-panel">
        {tabBar}
        <VcgPanel prices={prices} marketState={marketState} />
      </div>
    );
  }

  if (activeTab === "gex") {
    return (
      <div className="regime-panel">
        {tabBar}
        <GexPanel marketState={marketState} />
      </div>
    );
  }

  if (!data && !syncing) {
    return (
      <div className="regime-panel">
        {tabBar}
        <div className="regime-empty">
          <Shield size={32} strokeWidth={1} />
          <p>No CRI data available. Click Sync Now to run a scan.</p>
        </div>
      </div>
    );
  }

  return (
    <div className="regime-panel">
      {tabBar}
      {/* ── Row 1: CRI Score Hero ──────────────────── */}
      <div className="regime-hero">
        <div className="regime-hero-score" style={{ color }}>
          {cri.score.toFixed(0)}
          <span className="regime-hero-max">/100</span>
        </div>
        <div className="regime-hero-meta">
          <span className="regime-level-badge" style={{ background: color, color: cri.level === "LOW" ? "#000" : "#fff" }}>
            {cri.level}
          </span>
          <span className="regime-live-dot" style={{ background: effectiveHasLive ? "var(--positive)" : "var(--text-muted)" }} />
          <span className="regime-hero-label">{effectiveHasLive ? "LIVE" : "CACHED"}</span>
          {lastSync && (
            <span className="regime-hero-timestamp">
              Last scan: {new Date(lastSync).toLocaleTimeString()}
            </span>
          )}
          {shareModal}
        </div>
        <div className="regime-hero-bar">
          <div className="regime-hero-bar-fill" style={{ width: `${cri.score}%`, background: color }} />
        </div>
        <div className="regime-hero-scale">
          <span>LOW</span><span>ELEVATED</span><span>HIGH</span><span>CRITICAL</span>
        </div>
      </div>

      {/* ── Market Closed Indicator ───────────────── */}
      {!marketOpen && (
        <div
          data-testid="market-closed-indicator"
          style={{
            display: "flex",
            alignItems: "center",
            gap: "8px",
            padding: "6px 12px",
            background: "rgba(245,166,35,0.12)",
            color: "var(--warning, #F5A623)",
            fontSize: "11px",
            fontFamily: "var(--font-mono, monospace)",
            letterSpacing: "0.08em",
            fontWeight: 600,
            borderLeft: "2px solid var(--warning, #F5A623)",
          }}
        >
          MARKET CLOSED — END OF DAY VALUES
        </div>
      )}

      {/* ── Row 2: Live Tickers Strip ─────────────── */}
      <RegimeStrip>
        <RegimeStripCell
          testId="strip-vix"
          label={<>VIX <LiveBadge live={effectiveHasLiveVix} /></>}
          value={fmt(vixVal)}
          change={<DayChange last={liveVix} close={vixClose} />}
          sub={<>5d RoC: {fmtPct(data?.vix_5d_roc, 1)}</>}
          timestamp={vixLastTs ?? "---"}
        />
        <RegimeStripCell
          testId="strip-vvix"
          label={<>VVIX <LiveBadge live={effectiveHasLiveVvix} /></>}
          value={fmt(vvixVal)}
          change={<DayChange last={liveVvix} close={vvixClose} />}
          sub={<>VVIX/VIX: {fmt(vvixVixRatio)}</>}
          timestamp={vvixLastTs ?? "---"}
        />
        <RegimeStripCell
          testId="strip-spy"
          label={<>SPY <LiveBadge live={effectiveHasLiveSpy} /></>}
          value={`$${fmt(spyVal)}`}
          change={<DayChange last={liveSpy} close={spyClose} prefix="$" />}
          sub={<>vs 100d MA: {fmtPct(spxDistPct)}</>}
        />
        <RegimeStripCell
          testId="strip-rvol"
          label={(
            <>
              <span className="regime-strip-label-text-full">REALIZED VOL</span>
              <span className="regime-strip-label-text-short">RVOL</span>
              <LiveBadge live={hasIntradayRvol} />
            </>
          )}
          value={activeRvol != null ? `${fmt(activeRvol)}%` : "---"}
          change={<PointChange change={intradayRvol != null && data?.realized_vol != null ? intradayRvol - data.realized_vol : null} suffix="%" label="intraday" />}
          sub={<>20d annualized</>}
        />
        <RegimeStripCell
          testId="strip-cor1m"
          label={<>COR1M <LiveBadge live={effectiveHasLiveCor1m} /></>}
          value={fmt(activeCorr, 2)}
          change={<DayChange last={liveCor1m} close={cor1mPreviousClose} />}
          sub={<>{`5d chg: ${corr5dChange != null ? `${fmtSigned(corr5dChange)} pts` : "---"}`}</>}
        />
        {nqSkew != null ? (
          <RegimeStripCell
            testId="strip-nq-skew"
            label={<>NQ SKEW <LiveBadge live={false} /></>}
            value={`${formattedNqSkew}`}
            change={null}
            sub={<>NQ - SPX</>}
          />
        ) : null}
      </RegimeStrip>

      {/* ── Row 3+4: Components + Crash Trigger side by side ── */}
      <div className="regime-detail-grid">
        <div className="regime-components">
          <div className="regime-panel-title">
            <Zap size={12} />
            CRI COMPONENTS
            <InfoTooltip text={SECTION_TOOLTIPS["CRI COMPONENTS"]} />
          </div>
          <ComponentBar label="VIX" score={cri.components.vix} live={effectiveHasLiveVix} />
          <ComponentBar label="VVIX" score={cri.components.vvix} live={effectiveHasLiveVvix} />
          <ComponentBar label="CORRELATION" score={cri.components.correlation} live={effectiveHasLiveCor1m} />
          <ComponentBar label="MOMENTUM" score={cri.components.momentum} live={effectiveHasLiveSpy} />
        </div>
        <div className="regime-triggers">
          <div className="regime-panel-title">
            <AlertTriangle size={12} />
            CRASH TRIGGER CONDITIONS
            <InfoTooltip text={SECTION_TOOLTIPS["CRASH TRIGGER CONDITIONS"]} />
          </div>
            <div className={`regime-trigger-status ${data?.crash_trigger?.triggered ? "regime-triggered" : ""}`}>
            {data?.crash_trigger?.triggered ? "TRIGGERED" : "INACTIVE"}
          </div>
            <TriggerRow
              label="SPX < 100d MA"
              met={spxBelowMa}
              value={`${fmtPct(spxDistPct)} (MA: $${fmt(ma)})`}
              live={effectiveHasLiveSpy}
            />
            <TriggerRow
              label="Realized Vol > 25%"
              met={data?.crash_trigger?.conditions.realized_vol_gt_25 ?? false}
              value={data?.realized_vol != null ? `${fmt(data.realized_vol)}%` : "---"}
              live={false}
            />
            <TriggerRow
              label="COR1M > 60"
              met={correlationTriggerMet}
              value={fmt(activeCorr, 2)}
              live={effectiveHasLiveCor1m}
            />
        </div>
      </div>

      {/* ── Row 5: 20-Session History Charts (side by side) ── */}

      {data?.history && data.history.length > 0 && (() => {
        const vixVvixSeries: [ChartSeries, ChartSeries] = [
          { key: "vix", label: "VIX", color: chartSeriesColor("primary"), axis: "left", format: (v: number) => v.toFixed(1) },
          { key: "vvix", label: "VVIX", color: chartSeriesColor("extreme"), axis: "right", format: (v: number) => v.toFixed(0) },
        ];
        const rvolCorrSeries: [ChartSeries, ChartSeries] = [
          { key: "realized_vol", label: "RVOL", color: chartSeriesColor("caution"), axis: "left", format: (v: number) => `${v.toFixed(1)}%` },
          { key: "cor1m", label: "COR1M", color: chartSeriesColor("dislocation"), axis: "right", format: (v: number) => v.toFixed(1) },
        ];
        // Live values for today's data point (VIX/VVIX chart)
        // Only inject live overlays when market is actually open.
        const vixVvixLive: Partial<Record<keyof CriHistoryEntry, number>> = {};
        if (marketOpen && liveVix != null) vixVvixLive.vix = liveVix;
        if (marketOpen && liveVvix != null) vixVvixLive.vvix = liveVvix;
        // Live values for today's data point (RVOL/COR1M chart)
        const rvolCorrLive: Partial<Record<keyof CriHistoryEntry, number>> = {};
        if (intradayRvol != null) rvolCorrLive.realized_vol = intradayRvol;
        if (marketOpen && liveCor1m != null) rvolCorrLive.cor1m = liveCor1m;

        return (
          <>
            <div className="section-header" data-testid="regime-history-header">
              <div className="section-title" data-testid="regime-history-title">
                <span data-testid="regime-history-title-text">20-SESSION HISTORY</span>
                <InfoTooltip
                  text={SECTION_TOOLTIPS["20-SESSION HISTORY"]}
                  ariaLabel="Explain the 20-session history charts"
                  triggerTestId="regime-history-tooltip-trigger"
                  contentTestId="regime-history-tooltip-bubble"
                />
              </div>
              {effectiveHasLive && (
                <span className="regime-badge" style={{ background: "var(--chart-live-badge-bg)", color: "var(--chart-live-badge-text)" }}>LIVE</span>
              )}
            </div>
            <div className="regime-history-grid" data-testid="regime-history-grid">
              <div data-testid="regime-history-chart-vix-vvix">
                <CriHistoryChart
                  history={data.history}
                  series={vixVvixSeries}
                  title="VIX / VVIX"
                  liveValues={Object.keys(vixVvixLive).length > 0 ? vixVvixLive : undefined}
                />
              </div>
              <div data-testid="regime-history-chart-rvol-cor1m">
                <CriHistoryChart
                  history={data.history}
                  series={rvolCorrSeries}
                  title="REALIZED VOL / COR1M"
                  liveValues={Object.keys(rvolCorrLive).length > 0 ? rvolCorrLive : undefined}
                />
              </div>
            </div>
            <RegimeRelationshipView
              history={data.history}
              liveValues={{
                realized_vol: activeRvol ?? undefined,
                cor1m: activeCorr ?? undefined,
              }}
            />
          </>
        );
      })()}

    </div>
  );
}
