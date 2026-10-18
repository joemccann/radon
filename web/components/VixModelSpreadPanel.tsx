"use client";

import { useState } from "react";
import styles from "./VixModelSpreadPanel.module.css";
import { Activity } from "lucide-react";
import BrushMinimap from "./BrushMinimap";
import FreshnessRail from "./FreshnessRail";
import HistoryRangeChips from "./HistoryRangeChips";
import InfoTooltip from "./InfoTooltip";
import MetricCell from "./mobile/MetricCell";
import RequestError from "./RequestError";
import { RegimeStrip, RegimeStripCell } from "./RegimeStrip";
import SectionEmptyState from "./SectionEmptyState";
import SpectralLoader from "./SpectralLoader";
import VixModelSpreadChart from "./VixModelSpreadChart";
import { presetRange, type RangePresetSlug } from "@/lib/historyRange";
import { VIX_MODEL_SPREAD_REFRESH } from "@/lib/refreshSchedule";
import { formatVolPoints, formatSpreadZ, spreadBandState, spreadWindowStats, type SpreadWindow } from "@/lib/vixModelSpread";
import { useVixModelSpread } from "@/lib/useVixModelSpread";
import { useViewport } from "@/lib/useViewport";

const EXPLANATION = "Model VIX = √(COR1M / 100) × VIXEQ. The spread is VIX minus model VIX in volatility points. A positive historical basis is normal. Bands use the selected trailing session window and population standard deviation, including the current observation. Above or below a band describes the spread, not a forecast of VIX declines.";

export default function VixModelSpreadPanel() {
  const { data, loading, syncing, error, syncNow } = useVixModelSpread();
  const { isMobile, hasMounted } = useViewport();
  const [window, setWindow] = useState<SpreadWindow>(20);
  const [preset, setPreset] = useState<RangePresetSlug | "custom" | null>(null);
  const [custom, setCustom] = useState<[number, number] | null>(null);
  const series = data?.series ?? [];
  const active = preset ?? (series.length >= 252 ? "1y" : "all");
  const max = Math.max(0, series.length - 1);
  const range: [number, number] = active === "custom" && custom
    ? [Math.max(0, Math.min(custom[0], max)), Math.max(0, Math.min(custom[1], max))]
    : presetRange(active === "custom" ? "all" : active, series.length);
  const requestError = <RequestError error={error} onRetry={syncNow} retainedData={Boolean(data?.current)} testId="vix-model-spread-error" />;
  if ((loading || syncing) && !data) return <>{requestError}<SpectralLoader label="Loading VIX model spread" /></>;
  if (!data || data.missing || !data.current || !series.length) return <>{requestError}<SectionEmptyState icon={Activity} headline="No aligned VIX model data" secondary="Waiting for VIX, VIXEQ and COR1M observations from the same session." /></>;
  const current = data.current;
  const stats = spreadWindowStats(current, window);
  const state = spreadBandState(current, window);
  const tone = state === "ABOVE BAND" ? "var(--negative)" : state === "BELOW BAND" ? "var(--positive)" : "var(--text-muted)";
  const metrics = [
    { key: "vix", label: "VIX", value: formatVolPoints(current.vix) },
    { key: "model", label: "Model VIX", value: formatVolPoints(current.model_vix) },
    { key: "mean", label: `${window}-session mean`, value: formatVolPoints(stats.mean) },
    { key: "z", label: `${window}-session z`, value: formatSpreadZ(stats.z) },
  ];
  return <>
    {requestError}
    <div className="section" data-testid="vix-model-spread-panel">
      <div className="section-header"><div className="section-title">VIX model spread<InfoTooltip text={EXPLANATION} /></div></div>
      <div style={{ padding: "16px", display: "flex", alignItems: "baseline", gap: 16, flexWrap: "wrap" }}>
        <span data-testid="vix-model-spread-hero" style={{ fontSize: "clamp(36px, 7vw, 56px)", fontVariantNumeric: "tabular-nums", fontWeight: 500, letterSpacing: "-0.04em", lineHeight: 1.1 }}>{formatVolPoints(current.spread)}<span style={{ fontSize: 14, color: "var(--text-muted)", letterSpacing: 0 }}> pts</span></span>
        <span data-testid="vix-model-spread-state" style={{ color: tone, fontSize: 12 }}>{state}</span>
      </div>
      {hasMounted && isMobile ? <div className="m-regime-grid2x2" data-testid="vix-model-spread-mobile-grid">{metrics.map((metric) => <MetricCell key={metric.key} label={metric.label} value={metric.value} size="secondary" testId={`vix-model-spread-${metric.key}`} />)}</div>
        : <RegimeStrip columns={4} cadence="daily">{metrics.map((metric) => <RegimeStripCell key={metric.key} testId={`vix-model-spread-${metric.key}`} label={metric.label} value={metric.value} />)}</RegimeStrip>}
      <FreshnessRail schedule={VIX_MODEL_SPREAD_REFRESH} asOf={data.data_date} testId="vix-model-spread-freshness-rail" asOfTestId="vix-model-spread-strip-asof" />
    </div>
    <div className="breadth-history-block" data-testid="vix-model-spread-chart-section">
      <div className={styles.controls}>
        <div role="group" aria-label="Statistical window" className="history-range-chips">{([20, 252] as const).map((value) => <button type="button" className={`history-range-chip${window === value ? " is-active" : ""}`} key={value} aria-pressed={window === value} onClick={() => setWindow(value)}>{value} sessions</button>)}</div>
        <HistoryRangeChips active={active} onChange={(value) => { setPreset(value); setCustom(null); }} maxSessions={series.length} ariaLabel="VIX model chart range" dataTestId="vix-model-spread-range-chips" />
      </div>
      <VixModelSpreadChart history={series.slice(range[0], range[1] + 1)} window={window} />
      {series.length >= 2 ? <BrushMinimap values={series.map((row) => Number.isFinite(row.spread) ? row.spread : null)} range={range} onRangeChange={setCustom} onCustom={() => setPreset("custom")} testIdPrefix="vix-model-spread-brush" ariaLabel="VIX model history range brush" /> : null}
      <div className="section-footnote" data-testid="vix-model-spread-source-footnote">Cboe VIX, VIXEQ and COR1M closes, aligned by session. VIXEQ history before its November 4, 2024 launch is backtested. Statistical windows use the full aligned history, independently of the visible range.</div>
    </div>
  </>;
}
