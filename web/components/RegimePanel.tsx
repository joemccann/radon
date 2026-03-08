"use client";

import { useMemo } from "react";
import { Activity, AlertTriangle, Check, Shield, X, Zap } from "lucide-react";
import type { PriceData } from "@/lib/pricesProtocol";
import { useRegime, type CriData } from "@/lib/useRegime";
import { computeCri, type CriLevel, type CriResult } from "@/lib/criCalc";

type RegimePanelProps = {
  prices: Record<string, PriceData>;
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

function LiveBadge({ live }: { live: boolean }) {
  return (
    <span
      className="regime-badge"
      style={{
        background: live ? "rgba(34,197,94,0.15)" : "rgba(255,255,255,0.06)",
        color: live ? "var(--positive)" : "var(--text-muted)",
      }}
    >
      {live ? "LIVE" : "DAILY"}
    </span>
  );
}

/* ─── Component Bar ──────────────────────────────────── */

function ComponentBar({ label, score, live }: { label: string; score: number; live: boolean }) {
  const pct = (score / 25) * 100;
  const barColor = score < 8 ? "var(--positive)" : score > 16 ? "var(--negative)" : "var(--warning)";
  return (
    <div className="regime-component-bar">
      <div className="regime-component-label">
        {label}
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

export default function RegimePanel({ prices }: RegimePanelProps) {
  const { data, syncing, lastSync } = useRegime(true);

  // Live prices from WS
  const liveVix = prices["VIX"]?.last ?? null;
  const liveVvix = prices["VVIX"]?.last ?? null;
  const liveSpy = prices["SPY"]?.last ?? null;
  const hasLive = liveVix != null || liveVvix != null || liveSpy != null;

  // Merge live + cached into CRI inputs
  const liveCri: CriResult | null = useMemo(() => {
    if (!data) return null;

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
      corr: data.avg_sector_correlation ?? 0,
      corr5dChange: data.corr_5d_change ?? 0,
      spxDistancePct,
    });
  }, [data, liveVix, liveVvix, liveSpy]);

  const cri = liveCri ?? (data?.cri ? { ...data.cri, level: data.cri.level as CriLevel } : { score: 0, level: "LOW" as CriLevel, components: { vix: 0, vvix: 0, correlation: 0, momentum: 0 } });
  const color = levelColor(cri.level);

  // Live-derived values
  const vixVal = liveVix ?? data?.vix ?? 0;
  const vvixVal = liveVvix ?? data?.vvix ?? 0;
  const spyVal = liveSpy ?? data?.spy ?? 0;
  const vvixVixRatio = vixVal > 0 ? vvixVal / vixVal : data?.vvix_vix_ratio ?? 0;
  const ma = data?.spx_100d_ma;
  const spxDistPct = ma && ma > 0 ? ((spyVal / ma) - 1) * 100 : data?.spx_distance_pct ?? 0;
  const spxBelowMa = ma ? spyVal < ma : data?.crash_trigger?.conditions.spx_below_100d_ma ?? false;

  if (!data && !syncing) {
    return (
      <div className="regime-panel">
        <div className="regime-empty">
          <Shield size={32} strokeWidth={1} />
          <p>No CRI data available. Click Sync Now to run a scan.</p>
        </div>
      </div>
    );
  }

  return (
    <div className="regime-panel">
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
          <span className="regime-live-dot" style={{ background: hasLive ? "var(--positive)" : "var(--text-muted)" }} />
          <span className="regime-hero-label">{hasLive ? "LIVE" : "CACHED"}</span>
          {lastSync && (
            <span className="regime-hero-timestamp">
              Last scan: {new Date(lastSync).toLocaleTimeString()}
            </span>
          )}
        </div>
        <div className="regime-hero-bar">
          <div className="regime-hero-bar-fill" style={{ width: `${cri.score}%`, background: color }} />
        </div>
        <div className="regime-hero-scale">
          <span>LOW</span><span>ELEVATED</span><span>HIGH</span><span>CRITICAL</span>
        </div>
      </div>

      {/* ── Row 2: Live Tickers Strip ─────────────── */}
      <div className="regime-strip">
        <div className="regime-strip-cell">
          <div className="regime-strip-label">VIX <LiveBadge live={liveVix != null} /></div>
          <div className="regime-strip-value">{fmt(vixVal)}</div>
          <div className="regime-strip-sub">5d RoC: {fmtPct(data?.vix_5d_roc, 1)}</div>
        </div>
        <div className="regime-strip-cell">
          <div className="regime-strip-label">VVIX <LiveBadge live={liveVvix != null} /></div>
          <div className="regime-strip-value">{fmt(vvixVal)}</div>
          <div className="regime-strip-sub">VVIX/VIX: {fmt(vvixVixRatio)}</div>
        </div>
        <div className="regime-strip-cell">
          <div className="regime-strip-label">SPY <LiveBadge live={liveSpy != null} /></div>
          <div className="regime-strip-value">${fmt(spyVal)}</div>
          <div className="regime-strip-sub">vs 100d MA: {fmtPct(spxDistPct)}</div>
        </div>
        <div className="regime-strip-cell">
          <div className="regime-strip-label">REALIZED VOL <LiveBadge live={false} /></div>
          <div className="regime-strip-value">{data?.realized_vol != null ? `${fmt(data.realized_vol)}%` : "---"}</div>
          <div className="regime-strip-sub">20d annualized</div>
        </div>
        <div className="regime-strip-cell">
          <div className="regime-strip-label">SECTOR CORR <LiveBadge live={false} /></div>
          <div className="regime-strip-value">{fmt(data?.avg_sector_correlation, 4)}</div>
          <div className="regime-strip-sub">5d chg: {data?.corr_5d_change != null ? fmtPct(data.corr_5d_change * 100, 2) : "---"}</div>
        </div>
      </div>

      {/* ── Row 3: Component Bars ─────────────────── */}
      <div className="section-header">
        <Zap size={14} />
        CRI COMPONENTS
      </div>
      <div className="regime-components">
        <ComponentBar label="VIX" score={cri.components.vix} live={liveVix != null} />
        <ComponentBar label="VVIX" score={cri.components.vvix} live={liveVvix != null} />
        <ComponentBar label="CORRELATION" score={cri.components.correlation} live={false} />
        <ComponentBar label="MOMENTUM" score={cri.components.momentum} live={liveSpy != null} />
      </div>

      {/* ── Row 4: Crash Trigger Panel ────────────── */}
      <div className="section-header">
        <AlertTriangle size={14} />
        CRASH TRIGGER CONDITIONS
      </div>
      <div className="regime-triggers">
        <div className={`regime-trigger-status ${data?.crash_trigger?.triggered ? "regime-triggered" : ""}`}>
          {data?.crash_trigger?.triggered ? "TRIGGERED" : "INACTIVE"}
        </div>
        <TriggerRow
          label="SPX < 100d MA"
          met={spxBelowMa}
          value={`${fmtPct(spxDistPct)} (MA: $${fmt(ma)})`}
          live={liveSpy != null}
        />
        <TriggerRow
          label="Realized Vol > 25%"
          met={data?.crash_trigger?.conditions.realized_vol_gt_25 ?? false}
          value={data?.realized_vol != null ? `${fmt(data.realized_vol)}%` : "---"}
          live={false}
        />
        <TriggerRow
          label="Avg Correlation > 0.60"
          met={data?.crash_trigger?.conditions.avg_correlation_gt_060 ?? false}
          value={fmt(data?.avg_sector_correlation, 4)}
          live={false}
        />
      </div>

      {/* ── Row 5: CTA Model + MenthorQ ───────────── */}
      <div className="section-header">
        <Activity size={14} />
        CTA EXPOSURE MODEL
      </div>
      <div className="regime-cta-grid">
        <div className="regime-cta-panel">
          <div className="regime-cta-title">VOL-TARGETING MODEL</div>
          <div className="regime-cta-rows">
            <div className="regime-cta-row">
              <span>Implied Exposure</span>
              <span className={data?.cta?.exposure_pct != null && data.cta.exposure_pct < 50 ? "text-negative" : ""}>
                {fmt(data?.cta?.exposure_pct, 1)}%
              </span>
            </div>
            <div className="regime-cta-row">
              <span>Forced Reduction</span>
              <span className={data?.cta?.forced_reduction_pct && data.cta.forced_reduction_pct > 0 ? "text-negative" : "text-positive"}>
                {fmt(data?.cta?.forced_reduction_pct, 1)}%
              </span>
            </div>
            <div className="regime-cta-row">
              <span>Est. CTA Selling</span>
              <span className={data?.cta?.est_selling_bn && data.cta.est_selling_bn > 50 ? "text-negative" : ""}>
                ${fmt(data?.cta?.est_selling_bn, 1)}B
              </span>
            </div>
          </div>
          {data?.cta && (
            <div className="regime-cta-gauge">
              <div className="regime-cta-gauge-label">EXPOSURE</div>
              <div className="regime-bar-track">
                <div
                  className="regime-bar-fill"
                  style={{
                    width: `${Math.min(data.cta.exposure_pct, 200) / 2}%`,
                    background: data.cta.exposure_pct >= 80 ? "var(--positive)" : "var(--negative)",
                  }}
                />
              </div>
              <div className="regime-cta-gauge-scale">
                <span>0%</span><span>100%</span><span>200%</span>
              </div>
            </div>
          )}
        </div>

        {data?.menthorq_cta?.spx ? (
          <div className="regime-cta-panel">
            <div className="regime-cta-title">MENTHORQ CTA — SPX</div>
            <div className="regime-cta-rows">
              <div className="regime-cta-row">
                <span>Position Today</span>
                <span>{fmt((data.menthorq_cta.spx as Record<string, number>).position_today)}</span>
              </div>
              <div className="regime-cta-row">
                <span>Position Yesterday</span>
                <span>{fmt((data.menthorq_cta.spx as Record<string, number>).position_yesterday)}</span>
              </div>
              <div className="regime-cta-row">
                <span>3M Percentile</span>
                <span>{String((data.menthorq_cta.spx as Record<string, unknown>).percentile_3m ?? "---")}</span>
              </div>
              <div className="regime-cta-row">
                <span>3M Z-Score</span>
                <span>{fmt((data.menthorq_cta.spx as Record<string, number>).z_score_3m)}</span>
              </div>
            </div>
            <div className="regime-cta-date">Data: {data.menthorq_cta.date ?? "---"}</div>
          </div>
        ) : (
          <div className="regime-cta-panel">
            <div className="regime-cta-title">MENTHORQ CTA</div>
            <div className="regime-cta-empty">Data unavailable</div>
          </div>
        )}
      </div>

      {/* ── Row 6: 10-Day History ─────────────────── */}
      {data?.history && data.history.length > 0 && (
        <>
          <div className="section-header">10-DAY HISTORY</div>
          <div className="regime-history">
            <table>
              <thead>
                <tr>
                  <th>DATE</th>
                  <th className="text-right">VIX</th>
                  <th className="text-right">VVIX</th>
                  <th className="text-right">SPY</th>
                  <th className="text-right">VS 100D MA</th>
                  <th className="text-right">VIX 5D ROC</th>
                </tr>
              </thead>
              <tbody>
                {data.history.map((h, i) => {
                  const isLast = i === data.history.length - 1;
                  return (
                    <tr key={h.date} className={isLast ? "regime-history-current" : ""}>
                      <td>{h.date}</td>
                      <td className="text-right">{h.vix.toFixed(2)}</td>
                      <td className="text-right">{h.vvix.toFixed(2)}</td>
                      <td className="text-right">${h.spy.toFixed(2)}</td>
                      <td className={`text-right ${h.spx_vs_ma_pct < -5 ? "text-negative" : h.spx_vs_ma_pct < 0 ? "text-warning" : ""}`}>
                        {fmtPct(h.spx_vs_ma_pct)}
                      </td>
                      <td className="text-right">{fmtPct(h.vix_5d_roc, 1)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}
