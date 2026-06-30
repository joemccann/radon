"use client";

import { InstrumentPanel, type PanelTone } from "@/components/instruments";
import type { CriData } from "@/lib/useRegime";
import type { VcgData } from "@/lib/useVcg";
import type { MarkovStateOutput } from "@/lib/useMarkovState";
import type { PortfolioData } from "@/lib/types";
import { optionKey, type PriceData } from "@/lib/pricesProtocol";

/* ────────────────────────────────────────────────────────────
   SLOT 1 — CRI Composite (Signal Candidate)
   ──────────────────────────────────────────────────────────── */

function criTone(level: string | undefined): PanelTone {
  switch (level) {
    case "LOW":
      return "core";
    case "ELEVATED":
      return "warn";
    case "HIGH":
      return "fault";
    case "CRITICAL":
      return "extreme";
    default:
      return "neutral";
  }
}

export function CriCompositeCard({ data }: { data: CriData | null }) {
  const cri = data?.cri;
  const tone = criTone(cri?.level);
  return (
    <InstrumentPanel
      eyebrow="Signal Candidate / 01"
      title="CRI Composite"
      badge={cri ? { text: cri.level, tone } : undefined}
      trace={tone}
      level={cri ? cri.score : undefined}
      metric={cri ? cri.score.toFixed(1) : "—"}
      metricLabel="Composite stress"
      awaiting={!cri}
      meta={[
        { k: "vix", v: data ? data.vix.toFixed(2) : "—" },
        { k: "engine", v: "Composite" },
        { k: "basis", v: "1d / 20d" },
      ]}
    />
  );
}

/* ────────────────────────────────────────────────────────────
   SLOT 2 — Vol Dislocation (Surface State / VCG)
   ──────────────────────────────────────────────────────────── */

function vcgTone(interpretation: string | undefined): PanelTone {
  switch (interpretation) {
    case "NORMAL":
    case "BOUNCE":
      return "core";
    case "WATCH":
      return "warn";
    case "EDR":
      return "warn";
    case "RISK_OFF":
      return "fault";
    case "PANIC":
      return "extreme";
    case "SUPPRESSED":
      return "neutral";
    default:
      return "neutral";
  }
}

function vcgBadgeLabel(interpretation: string | undefined): string {
  switch (interpretation) {
    case "RISK_OFF":
      return "Risk-off";
    case "EDR":
      return "Early divergence";
    case "WATCH":
      return "Watch";
    case "BOUNCE":
      return "Bounce";
    case "NORMAL":
      return "Normal";
    case "PANIC":
      return "Panic";
    case "SUPPRESSED":
      return "Suppressed";
    default:
      return "—";
  }
}

function formatZ(z: number | null | undefined): string {
  if (z == null || !Number.isFinite(z)) return "—";
  return z >= 0 ? `+${z.toFixed(2)}σ` : `${z.toFixed(2)}σ`;
}

export function VolDislocationCard({ data }: { data: VcgData | null }) {
  const signal = data?.signal;
  const tone = vcgTone(signal?.interpretation);
  const ratio = data && data.signal.vix > 0
    ? data.signal.vvix / data.signal.vix
    : null;
  const sigma = signal?.residual ?? signal?.vcg_adj ?? null;
  const level = sigma != null && Number.isFinite(sigma)
    ? Math.min(100, (Math.abs(sigma) / 3) * 100) : undefined;
  return (
    <InstrumentPanel
      eyebrow="Surface State / 02"
      title="Vol-Credit Gap"
      badge={signal ? { text: vcgBadgeLabel(signal.interpretation), tone } : undefined}
      trace={tone}
      level={level}
      metric={formatZ(signal?.residual ?? signal?.vcg_adj ?? null)}
      metricLabel="20-session baseline deviation"
      awaiting={!signal}
      meta={[
        { k: "vvix.vix.ratio", v: ratio != null ? ratio.toFixed(2) : "—" },
        { k: "engine", v: "Eigen" },
        { k: "basis", v: "20d residual" },
      ]}
    />
  );
}

/* ────────────────────────────────────────────────────────────
   SLOT 3 — Markov State (Regime Path)
   ──────────────────────────────────────────────────────────── */

function markovTone(band: string | null): PanelTone {
  switch (band) {
    case "LOW":
      return "core";
    case "ELEVATED":
      return "warn";
    case "HIGH":
      return "fault";
    case "CRITICAL":
      return "extreme";
    default:
      return "neutral";
  }
}

function bandIndex(band: string | null): string {
  switch (band) {
    case "LOW":
      return "1";
    case "ELEVATED":
      return "2";
    case "HIGH":
      return "3";
    case "CRITICAL":
      return "4";
    default:
      return "—";
  }
}

export function MarkovStateCard({ state }: { state: MarkovStateOutput }) {
  const tone = markovTone(state.currentBand);
  const arrow = state.currentBand && state.nextLikelyBand
    ? `${bandIndex(state.currentBand)} → ${bandIndex(state.nextLikelyBand)}`
    : "—";
  const level = state.pCurrent != null
    ? state.pCurrent * 100
    : state.currentBand ? Number(bandIndex(state.currentBand)) * 25 : undefined;
  return (
    <InstrumentPanel
      eyebrow="Regime Path / 03"
      title="Markov State"
      badge={
        state.currentBand
          ? { text: state.currentBand, tone }
          : undefined
      }
      trace={tone}
      level={level}
      metric={arrow}
      metricLabel="Transition bias"
      awaiting={state.currentBand == null}
      meta={[
        {
          k: "p(current)",
          v: state.pCurrent != null ? state.pCurrent.toFixed(2) : "—",
        },
        {
          k: "p(next)",
          v: state.pNext != null ? state.pNext.toFixed(2) : "—",
        },
        {
          k: "basis",
          v: state.sampleSize > 0 ? `${state.sampleSize}d matrix` : "—",
        },
      ]}
    />
  );
}

/* ────────────────────────────────────────────────────────────
   SLOT 4 — Portfolio Convexity (Exposure / Laplace)
   ──────────────────────────────────────────────────────────── */

function gammaTone(netGamma: number | null): PanelTone {
  if (netGamma == null) return "neutral";
  if (Math.abs(netGamma) < 0.2) return "core";
  if (Math.abs(netGamma) < 1.0) return "warn";
  return "dislocation";
}

function formatSigned(n: number | null | undefined, decimals = 2): string {
  if (n == null || !Number.isFinite(n)) return "—";
  return n >= 0 ? `+${n.toFixed(decimals)}` : n.toFixed(decimals);
}

/** Aggregate net gamma + net vega across every option leg in the portfolio.
 *  Per-leg contribution: sign × greek × contracts. */
function aggregateGreeks(
  portfolio: PortfolioData,
  prices: Record<string, PriceData | undefined>,
): { gamma: number; vega: number; legsCounted: number } {
  let gamma = 0;
  let vega = 0;
  let legsCounted = 0;
  for (const pos of portfolio.positions) {
    if (pos.structure_type === "Stock") continue;
    for (const leg of pos.legs) {
      if (!leg.strike || !leg.type || !pos.expiry) continue;
      const right = leg.type === "Call" ? "C" : "P";
      const expiryClean = pos.expiry.replace(/-/g, "");
      const key = optionKey({
        symbol: pos.ticker,
        expiry: expiryClean,
        strike: leg.strike,
        right,
      });
      const p = prices[key];
      if (!p || p.gamma == null || p.vega == null) continue;
      const sign = leg.direction === "SHORT" ? -1 : 1;
      gamma += sign * p.gamma * leg.contracts;
      vega += sign * p.vega * leg.contracts;
      legsCounted += 1;
    }
  }
  return { gamma, vega, legsCounted };
}

export function PortfolioConvexityCard({
  portfolio,
  prices,
}: {
  portfolio: PortfolioData | null;
  prices: Record<string, PriceData | undefined>;
}) {
  let netGamma: number | null = null;
  let netVega: number | null = null;
  let cashPct: number | null = null;

  if (portfolio) {
    const agg = aggregateGreeks(portfolio, prices);
    if (agg.legsCounted > 0) {
      netGamma = agg.gamma;
      netVega = agg.vega;
    }
    const nav = portfolio.account_summary?.net_liquidation;
    const cash = portfolio.account_summary?.cash;
    if (nav && nav > 0 && cash != null) {
      cashPct = (cash / nav) * 100;
    }
  }

  const tone = gammaTone(netGamma);
  const ready = portfolio != null && netGamma != null;
  const level = netGamma != null
    ? Math.min(100, (Math.abs(netGamma) / 2) * 100) : undefined;
  return (
    <InstrumentPanel
      eyebrow="Exposure / 04"
      title="Portfolio Convexity"
      badge={
        ready
          ? {
              text: tone === "core" ? "Stable" : tone === "warn" ? "Skewed" : "Dislocated",
              tone,
            }
          : undefined
      }
      trace={tone}
      level={level}
      metric={formatSigned(netGamma)}
      metricLabel="Net gamma"
      awaiting={!ready}
      meta={[
        { k: "net.vega", v: formatSigned(netVega) },
        { k: "net.cash", v: cashPct != null ? `${cashPct.toFixed(1)}%` : "—" },
        { k: "engine", v: "Laplace" },
      ]}
    />
  );
}
