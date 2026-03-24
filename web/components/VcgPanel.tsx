"use client";

import { useState } from "react";
import { AlertTriangle, TrendingUp, Zap } from "lucide-react";
import InfoTooltip from "./InfoTooltip";
import ShareReportModal from "./ShareReportModal";
import { useVcg, type VcgData, type VcgHistoryEntry } from "@/lib/useVcg";
import { MarketState } from "@/lib/useMarketHours";
import type { PriceData } from "@/lib/pricesProtocol";

type VcgPanelProps = {
  prices: Record<string, PriceData>;
  marketState?: MarketState;
};

/* ─── Helpers ─────────────────────────────────────────── */

function fmtZ(v: number | null): string {
  if (v == null) return "---";
  return v >= 0 ? `+${v.toFixed(2)}` : v.toFixed(2);
}

function fmtPct(v: number | null | undefined): string {
  if (v == null) return "---";
  return `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;
}

function fmtNum(v: number | null | undefined, decimals = 2): string {
  if (v == null) return "---";
  return v.toFixed(decimals);
}

function interpretationColor(interpretation: string): string {
  switch (interpretation) {
    case "RISK_OFF":   return "var(--fault)";
    case "EDR":        return "var(--warning)";
    case "WATCH":      return "var(--warning)";
    case "BOUNCE":     return "var(--signal-core)";
    case "NORMAL":     return "var(--signal-core)";
    case "PANIC":      return "var(--extreme)";
    case "SUPPRESSED": return "var(--text-muted)";
    default:           return "var(--text-muted)";
  }
}

function interpretationLabel(interpretation: string): string {
  switch (interpretation) {
    case "RISK_OFF":   return "RISK-OFF";
    case "EDR":        return "EARLY DIVERGENCE";
    case "WATCH":      return "WATCH";
    case "BOUNCE":     return "BOUNCE";
    case "NORMAL":     return "NORMAL";
    case "PANIC":      return "PANIC";
    case "SUPPRESSED": return "SUPPRESSED";
    default:           return "INSUFFICIENT DATA";
  }
}

function regimeBadgeColor(regime: string): string {
  switch (regime) {
    case "PANIC":      return "var(--extreme)";
    case "TRANSITION": return "var(--warning)";
    default:           return "var(--signal-core)";
  }
}

function tierColor(tier: 1 | 2 | 3 | null): string {
  switch (tier) {
    case 1: return "var(--fault)";
    case 2: return "var(--fault)";
    case 3: return "var(--warning)";
    default: return "var(--text-muted)";
  }
}

function tierLabel(tier: 1 | 2 | 3 | null): string {
  switch (tier) {
    case 1: return "TIER 1 — CRITICAL";
    case 2: return "TIER 2 — HIGH";
    case 3: return "TIER 3 — ELEVATED";
    default: return "NO ACTIVE TIER";
  }
}

function vvixSeverityColor(sev: string): string {
  switch (sev) {
    case "extreme":  return "var(--fault)";
    case "elevated": return "var(--warning)";
    default:         return "var(--signal-core)";
  }
}

function vvixSeverityDesc(sev: string): string {
  switch (sev) {
    case "extreme":  return "VVIX far above 120 — maximum vol-of-vol stress";
    case "elevated": return "VVIX above 110 — second-order stress signal";
    default:         return "VVIX below 110 — vol regime stable";
  }
}

/* ─── Sortable history ───────────────────────────────── */

type VcgSortCol = "date" | "vcg" | "vcg_adj" | "residual" | "beta1" | "beta2" | "vix" | "vvix" | "credit";
type SortDir = "asc" | "desc";

function sortIndicator(col: VcgSortCol, activeCol: VcgSortCol | null, dir: SortDir): string {
  if (col !== activeCol) return "";
  return dir === "asc" ? " ↑" : " ↓";
}

function sortHistory(
  rows: VcgHistoryEntry[],
  col: VcgSortCol | null,
  dir: SortDir,
): VcgHistoryEntry[] {
  if (!col) return rows;
  const sorted = [...rows].sort((a, b) => {
    const av = col === "date" ? a.date : (a[col] ?? -Infinity);
    const bv = col === "date" ? b.date : (b[col] ?? -Infinity);
    if (av < bv) return dir === "asc" ? -1 : 1;
    if (av > bv) return dir === "asc" ? 1 : -1;
    return 0;
  });
  return sorted;
}

/* ─── Main component ─────────────────────────────────── */

export default function VcgPanel({ marketState }: VcgPanelProps) {
  const { data, loading, error, lastSync } = useVcg(marketState ?? null);
  const [sortCol, setSortCol] = useState<VcgSortCol | null>(null);
  const [sortDir, setSortDir] = useState<SortDir>("desc");

  function handleSort(col: VcgSortCol) {
    if (sortCol === col) {
      if (sortDir === "desc") {
        setSortDir("asc");
      } else {
        setSortCol(null);
        setSortDir("desc");
      }
    } else {
      setSortCol(col);
      setSortDir("desc");
    }
  }

  if (loading && !data) {
    return (
      <div className="section">
        <div className="section-header">
          <div className="section-title">
            <Zap size={14} />
            Volatility-Credit Gap
          </div>
        </div>
        <div className="section-body" style={{ padding: "24px", textAlign: "center" }}>
          <span style={{ fontFamily: "var(--font-mono)", fontSize: "11px", color: "var(--text-muted)" }}>Loading VCG scan...</span>
        </div>
      </div>
    );
  }

  if (error && !data) {
    return (
      <div className="section">
        <div className="section-header">
          <div className="section-title">
            <Zap size={14} />
            Volatility-Credit Gap
          </div>
        </div>
        <div className="section-body" style={{ padding: "16px" }}>
          <div className="alert-item bearish">{error}</div>
        </div>
      </div>
    );
  }

  if (!data) return null;

  const sig = data.signal;
  const attr = sig.attribution;
  const interpColor = interpretationColor(sig.interpretation);

  return (
    <>
      {/* ── Signal strip ──────────────────────────────────── */}
      <div className="section">
        <div className="section-header">
          <div className="section-title">
            <Zap size={14} />
            VCG Signal
            <InfoTooltip text="Volatility-Credit Gap: detects divergence between the vol complex (VIX/VVIX) and credit markets (HYG). Signals: RISK_OFF (tier 1–2), EDR (early divergence), BOUNCE (counter-signal), NORMAL." />
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: "6px", flexWrap: "wrap" }}>
            {/* Regime badge */}
            <span className="pill" style={{ background: regimeBadgeColor(sig.regime), color: "#fff", fontSize: "9px" }}>
              {sig.regime}
            </span>
            {/* RISK-OFF */}
            {sig.ro === 1 && (
              <span className="pill" style={{ background: "var(--fault)", color: "#fff", fontSize: "9px" }}>
                <AlertTriangle size={10} style={{ marginRight: "3px" }} />
                RISK-OFF
              </span>
            )}
            {/* EDR (only when not already RISK-OFF) */}
            {sig.edr === 1 && sig.ro !== 1 && (
              <span className="pill" style={{ background: "var(--warning)", color: "#000", fontSize: "9px", fontWeight: 700 }}>
                EDR
              </span>
            )}
            {/* Tier badge */}
            {sig.tier != null && (
              <span className="pill" style={{ background: tierColor(sig.tier), color: "#fff", fontSize: "9px" }}>
                T{sig.tier}
              </span>
            )}
            {/* Bounce */}
            {sig.bounce === 1 && (
              <span className="pill" style={{ background: "var(--signal-core)", color: "#000", fontSize: "9px", fontWeight: 700 }}>
                <TrendingUp size={10} style={{ marginRight: "3px" }} />
                BOUNCE
              </span>
            )}
            <span style={{ fontFamily: "var(--font-mono)", fontSize: "9px", color: "var(--text-muted)" }}>
              {data.credit_proxy}
            </span>
            <ShareReportModal
              modalTitle="VCG REPORT — SHARE TO X"
              shareEndpoint="/api/vcg/share"
              buttonTitle="Share VCG report to X"
              iconSize={11}
              shareContentTitle="VCG Share Preview"
            />
            {lastSync && (
              <span style={{ fontFamily: "var(--font-mono)", fontSize: "9px", color: "var(--text-muted)" }}>
                {new Date(lastSync).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" })}
              </span>
            )}
          </div>
        </div>

        <div className="metrics-grid">
          <div className="metric-card">
            <div className="metric-label">VCG Z-Score</div>
            <div className="metric-value" style={{ color: interpColor }}>
              {fmtZ(sig.vcg)}
            </div>
            <div className="metric-change" style={{ color: interpColor }}>
              {interpretationLabel(sig.interpretation)}
            </div>
          </div>
          <div className="metric-card">
            <div className="metric-label">VCG Adj (Panic-Adj)</div>
            <div className="metric-value">{fmtZ(sig.vcg_adj)}</div>
            <div className="metric-change neutral">
              {sig.pi_panic > 0 ? `π = ${sig.pi_panic.toFixed(2)} SUPPRESSED` : "NO SUPPRESSION"}
            </div>
          </div>
          <div className="metric-card">
            <div className="metric-label">Credit 5d Return</div>
            <div className={`metric-value ${sig.credit_5d_return_pct >= 0 ? "positive" : "negative"}`}>
              {fmtPct(sig.credit_5d_return_pct)}
            </div>
            <div className="metric-change neutral">{data.credit_proxy} @ ${fmtNum(sig.credit_price)}</div>
          </div>
          <div className="metric-card">
            <div className="metric-label">Residual</div>
            <div className="metric-value">{sig.residual != null ? sig.residual.toFixed(6) : "---"}</div>
            <div className="metric-change neutral">MODEL ε</div>
          </div>
        </div>
      </div>

      {/* ── Signal Detail + Attribution ───────────────────── */}
      <div className="section">
        <div className="section-header">
          <div className="section-title">
            <Zap size={14} />
            Signal Detail
            <InfoTooltip text="Severity tier (1=critical, 2=high, 3=elevated), VVIX amplifier, and bounce conditions. Tier activates when ro=1 (Tier 1/2) or edr=1 (Tier 3)." />
          </div>
          {/* Overall signal pill */}
          <span
            className="pill"
            style={{
              background: interpColor,
              color: sig.interpretation === "NORMAL" || sig.interpretation === "BOUNCE" ? "#000" : "#fff",
              fontSize: "9px",
            }}
          >
            {interpretationLabel(sig.interpretation)}
          </span>
        </div>

        <div className="metrics-grid" style={{ gridTemplateColumns: "1fr 1fr" }}>
          {/* Left: Tier + VVIX severity */}
          <div className="metric-card" style={{ padding: "12px 16px" }}>
            {/* Severity tier row */}
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", paddingBottom: "8px", borderBottom: "1px solid var(--line-grid)" }}>
              <span style={{ fontFamily: "var(--font-mono)", fontSize: "10px", textTransform: "uppercase", letterSpacing: "0.08em", color: "var(--text-muted)" }}>
                Severity Tier
              </span>
              <span
                style={{
                  fontFamily: "var(--font-mono)",
                  fontSize: "11px",
                  fontWeight: 700,
                  color: tierColor(sig.tier),
                  background: sig.tier != null ? `${tierColor(sig.tier)}18` : "transparent",
                  padding: "2px 8px",
                  borderRadius: "999px",
                  border: sig.tier != null ? `1px solid ${tierColor(sig.tier)}40` : "none",
                }}
              >
                {tierLabel(sig.tier)}
              </span>
            </div>
            {/* VVIX severity row */}
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "8px 0", borderBottom: "1px solid var(--line-grid)" }}>
              <div>
                <div style={{ fontFamily: "var(--font-mono)", fontSize: "10px", textTransform: "uppercase", letterSpacing: "0.08em", color: "var(--text-muted)" }}>
                  VVIX Severity
                </div>
                <div style={{ fontFamily: "var(--font-mono)", fontSize: "9px", color: "var(--text-muted)", marginTop: "2px" }}>
                  {vvixSeverityDesc(sig.vvix_severity)}
                </div>
              </div>
              <span
                style={{
                  fontFamily: "var(--font-mono)",
                  fontSize: "11px",
                  fontWeight: 700,
                  color: vvixSeverityColor(sig.vvix_severity),
                  textTransform: "uppercase",
                  marginLeft: "12px",
                  flexShrink: 0,
                }}
              >
                {sig.vvix_severity}
              </span>
            </div>
            {/* EDR / Bounce rows */}
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "8px 0" }}>
              <span style={{ fontFamily: "var(--font-mono)", fontSize: "10px", color: "var(--text-muted)" }}>EDR</span>
              <span style={{ fontFamily: "var(--font-mono)", fontSize: "11px", fontWeight: 700, color: sig.edr === 1 ? "var(--warning)" : "var(--text-muted)" }}>
                {sig.edr === 1 ? "ACTIVE" : "INACTIVE"}
              </span>
            </div>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", paddingTop: "0" }}>
              <span style={{ fontFamily: "var(--font-mono)", fontSize: "10px", color: "var(--text-muted)" }}>Bounce</span>
              <span style={{ fontFamily: "var(--font-mono)", fontSize: "11px", fontWeight: 700, color: sig.bounce === 1 ? "var(--signal-core)" : "var(--text-muted)" }}>
                {sig.bounce === 1 ? "DETECTED" : "—"}
              </span>
            </div>
          </div>

          {/* Right: Attribution bars */}
          <div className="metric-card" style={{ padding: "12px 16px" }}>
            <div style={{ fontFamily: "var(--font-mono)", fontSize: "10px", textTransform: "uppercase", letterSpacing: "0.1em", color: "var(--text-muted)", marginBottom: "8px" }}>
              Attribution
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "8px" }}>
              <div style={{ flex: 1, height: "6px", borderRadius: "3px", background: "var(--bg-panel-raised)", overflow: "hidden" }}>
                <div style={{ width: `${Math.max(attr.vvix_pct, 0)}%`, height: "100%", background: "var(--extreme)", borderRadius: "3px" }} />
              </div>
              <span style={{ fontFamily: "var(--font-mono)", fontSize: "11px", color: "var(--text-primary)", minWidth: "60px" }}>
                VVIX {attr.vvix_pct.toFixed(0)}%
              </span>
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "12px" }}>
              <div style={{ flex: 1, height: "6px", borderRadius: "3px", background: "var(--bg-panel-raised)", overflow: "hidden" }}>
                <div style={{ width: `${Math.max(attr.vix_pct, 0)}%`, height: "100%", background: "var(--signal-core)", borderRadius: "3px" }} />
              </div>
              <span style={{ fontFamily: "var(--font-mono)", fontSize: "11px", color: "var(--text-primary)", minWidth: "60px" }}>
                VIX {attr.vix_pct.toFixed(0)}%
              </span>
            </div>
            <div style={{ fontFamily: "var(--font-mono)", fontSize: "10px", color: "var(--text-muted)", borderTop: "1px solid var(--line-grid)", paddingTop: "8px" }}>
              β₁(VVIX) = {fmtNum(sig.beta1_vvix, 6)} | β₂(VIX) = {fmtNum(sig.beta2_vix, 6)}
              {sig.sign_suppressed && (
                <span style={{ color: "var(--warning)", marginLeft: "8px" }}>SIGN REVERSED</span>
              )}
            </div>
            {/* VVIX level */}
            <div style={{ fontFamily: "var(--font-mono)", fontSize: "10px", color: "var(--text-muted)", marginTop: "6px" }}>
              VVIX {fmtNum(sig.vvix)} · VIX {fmtNum(sig.vix)}
            </div>
          </div>
        </div>
      </div>

      {/* ── History table (sortable) ────────────────────── */}
      <div className="section">
        <div className="section-header">
          <div className="section-title">VCG History (20d)</div>
        </div>
        <div className="section-body table-wrap">
          <table>
            <thead>
              <tr>
                {([
                  ["date", "Date", false],
                  ["vcg", "VCG", true],
                  ["vcg_adj", "VCG Adj", true],
                  ["residual", "Residual", true],
                  ["beta1", "β₁ (VVIX)", true],
                  ["beta2", "β₂ (VIX)", true],
                  ["vix", "VIX", true],
                  ["vvix", "VVIX", true],
                  ["credit", data.credit_proxy, true],
                ] as [VcgSortCol, string, boolean][]).map(([col, label, isRight]) => (
                  <th
                    key={col}
                    className={`${isRight ? "right" : ""} sortable-th`}
                    onClick={() => handleSort(col)}
                    style={{ cursor: "pointer", userSelect: "none", whiteSpace: "nowrap" }}
                  >
                    {label}{sortIndicator(col, sortCol, sortDir)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {sortHistory(data.history, sortCol, sortDir).map((h: VcgHistoryEntry) => (
                <tr key={h.date}>
                  <td>{h.date}</td>
                  <td className="right" style={{ color: (h.vcg ?? 0) > 2 ? "var(--fault)" : (h.vcg ?? 0) < -2 ? "var(--warning)" : "var(--text-primary)" }}>
                    {fmtZ(h.vcg)}
                  </td>
                  <td className="right">{fmtZ(h.vcg_adj)}</td>
                  <td className="right">{h.residual != null ? h.residual.toFixed(6) : "---"}</td>
                  <td className="right">{h.beta1 != null ? h.beta1.toFixed(6) : "---"}</td>
                  <td className="right">{h.beta2 != null ? h.beta2.toFixed(6) : "---"}</td>
                  <td className="right">{h.vix.toFixed(2)}</td>
                  <td className="right">{h.vvix.toFixed(2)}</td>
                  <td className="right">{h.credit.toFixed(2)}</td>
                </tr>
              ))}
              {data.history.length === 0 && (
                <tr>
                  <td colSpan={9} style={{ textAlign: "center", color: "var(--text-muted)" }}>No history data</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
}
