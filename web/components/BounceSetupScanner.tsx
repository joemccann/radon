"use client";

import { Fragment, useState } from "react";
import Link from "next/link";
import { Activity, Loader2 } from "lucide-react";
import InfoTooltip from "./InfoTooltip";
import ScannerInstrumentShell from "./ScannerInstrumentShell";
import SectionEmptyState from "./SectionEmptyState";
import RequestError from "./RequestError";
import TickerLink from "./TickerLink";
import {
  bounceOrderHref,
  bounceVerdictLabel,
  isFlowAccumulation,
  type BounceSeriesPoint,
  type BounceSetupData,
  type BounceSetupRow,
} from "@/lib/bounceSetup";

type BounceSetupScannerProps = {
  data: BounceSetupData | null;
  loading?: boolean;
  scanning?: boolean;
  error?: string | null;
  onRetry?: () => void;
  lastSync?: string | null;
  onScan?: () => void;
};

const SECTION_HELP =
  "Ranks the universe by how stretched each name is to the downside (RSI, Bollinger percent B, 20-session return z-score); rank 1 is the most stretched to the downside in the universe. Three legs over the last 20 sessions: spot is stretched lower, at-the-money fixed-strike vol ran up and is now off its peak, and 30-day put skew is easing from its high. All three make BOUNCE SETUP; fewer make WATCH or STRETCHED. BOUNCE SETUP names a pattern, not a forecast. OPEN TRADE also requires dark-pool flow accumulation on the ticker; without it the row reads NO FLOW EDGE.";

function fmt(value: number | null | undefined, digits = 1): string {
  if (value == null || !Number.isFinite(value)) return "---";
  return value.toFixed(digits);
}

function fmtPct(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "---";
  return `${value.toFixed(1)}%`;
}

function verdictTone(verdict: string): string {
  if (verdict === "BOUNCE_SETUP") return "pos";
  if (verdict === "WATCH") return "warn";
  return "mut";
}

function flowLabel(row: BounceSetupRow): string {
  if (!row.flow) return "---";
  return `${row.flow.signal} ${fmt(row.flow.score, 0)}`;
}

function ActionCell({ row }: { row: BounceSetupRow }) {
  if (row.verdict !== "BOUNCE_SETUP") return <span className="report-meta">---</span>;
  const href = isFlowAccumulation(row.flow) ? bounceOrderHref(row) : null;
  if (!href) return <span className="report-meta">NO FLOW EDGE</span>;
  const ticker = row.ticker.toUpperCase();
  return (
    <Link
      href={href}
      className="ticker-link"
      data-testid={`bounce-open-trade-${ticker}`}
      title={`Open a ${ticker} call spread in the chain`}
      onClick={(e) => e.stopPropagation()}
    >
      OPEN TRADE
    </Link>
  );
}

/* ─── Detail chart ─── */

const W = 640;
const H = 220;
const PAD = { l: 44, r: 44, t: 12, b: 24 };

function extent(values: number[]): [number, number] {
  if (values.length === 0) return [0, 1];
  let lo = Math.min(...values);
  let hi = Math.max(...values);
  if (lo === hi) {
    lo -= 1;
    hi += 1;
  }
  return [lo, hi];
}

function finite(v: number | null | undefined): v is number {
  return typeof v === "number" && Number.isFinite(v);
}

/** Line path that breaks at null / non-finite points instead of emitting NaN. */
function linePath(points: { x: number; y: number | null }[]): string {
  let d = "";
  let pen = false;
  for (const p of points) {
    if (p.y == null || !Number.isFinite(p.y) || !Number.isFinite(p.x)) {
      pen = false;
      continue;
    }
    d += `${pen ? "L" : "M"}${p.x.toFixed(1)},${p.y.toFixed(1)}`;
    pen = true;
  }
  return d;
}

function BounceDetailChart({ ticker, series }: { ticker: string; series: BounceSeriesPoint[] }) {
  const n = series.length;
  const innerW = W - PAD.l - PAD.r;
  const innerH = H - PAD.t - PAD.b;
  const x = (i: number) => PAD.l + (n <= 1 ? innerW / 2 : (i / (n - 1)) * innerW);

  // Left axis: spot cumulative % and fixed-strike vol change share one scale.
  const leftVals = series.flatMap((p) => [p.spot_cum_pct, p.fs_iv_change, 0]).filter(finite);
  const [lLo, lHi] = extent(leftVals);
  const yL = (v: number) => PAD.t + ((lHi - v) / (lHi - lLo)) * innerH;

  // Right axis: SKEW30 inverted, so higher skew plots lower.
  const [sLo, sHi] = extent(series.map((p) => p.skew30).filter(finite));
  const yR = (v: number) => PAD.t + ((v - sLo) / (sHi - sLo)) * innerH;

  const barW = Math.max(2, (innerW / Math.max(1, n)) * 0.6);
  const zero = yL(0);

  const spotD = linePath(series.map((p, i) => ({ x: x(i), y: finite(p.spot_cum_pct) ? yL(p.spot_cum_pct) : null })));
  const skewD = linePath(series.map((p, i) => ({ x: x(i), y: finite(p.skew30) ? yR(p.skew30) : null })));

  return (
    <div data-testid={`bounce-detail-chart-${ticker}`} style={{ padding: "8px 0" }}>
      <div className="report-meta" style={{ display: "flex", gap: 16, flexWrap: "wrap", marginBottom: 4 }}>
        <span style={{ color: "var(--signal-core)" }}>ATM FIXED STRIKE VOL</span>
        <span style={{ color: "var(--text-primary)" }}>SPOT</span>
        <span style={{ color: "var(--warning)" }}>SKEW30 (inverted)</span>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label={`${ticker} spot, fixed strike vol and skew over ${n} sessions`}>
        <line x1={PAD.l} x2={W - PAD.r} y1={zero} y2={zero} stroke="var(--line-grid)" strokeWidth={1} />
        {series.map((p, i) => {
          if (!finite(p.fs_iv_change)) return null;
          const y = yL(p.fs_iv_change);
          return (
            <rect
              key={`bar-${p.date}-${i}`}
              x={x(i) - barW / 2}
              y={Math.min(y, zero)}
              width={barW}
              height={Math.max(1, Math.abs(zero - y))}
              fill="color-mix(in srgb, var(--signal-core) 45%, transparent)"
            />
          );
        })}
        {spotD && <path d={spotD} fill="none" stroke="var(--text-primary)" strokeWidth={1.5} />}
        {skewD && <path d={skewD} fill="none" stroke="var(--warning)" strokeWidth={1.5} strokeDasharray="4 3" />}
        <text x={PAD.l - 6} y={PAD.t + 8} textAnchor="end" fontSize={10} fill="var(--text-muted)">{fmt(lHi)}</text>
        <text x={PAD.l - 6} y={H - PAD.b} textAnchor="end" fontSize={10} fill="var(--text-muted)">{fmt(lLo)}</text>
        <text x={W - PAD.r + 6} y={PAD.t + 8} fontSize={10} fill="var(--text-muted)">{fmt(sLo, 2)}</text>
        <text x={W - PAD.r + 6} y={H - PAD.b} fontSize={10} fill="var(--text-muted)">{fmt(sHi, 2)}</text>
        {n > 0 && (
          <>
            <text x={PAD.l} y={H - 6} fontSize={10} fill="var(--text-muted)">{series[0].date}</text>
            <text x={W - PAD.r} y={H - 6} textAnchor="end" fontSize={10} fill="var(--text-muted)">{series[n - 1].date}</text>
          </>
        )}
      </svg>
    </div>
  );
}

const COLS = 10;

export default function BounceSetupScanner({
  data,
  loading = false,
  scanning = false,
  error = null,
  onRetry,
  lastSync = null,
  onScan,
}: BounceSetupScannerProps) {
  const rows = data?.missing ? [] : [...(data?.results ?? [])].sort((a, b) => a.stretch_rank - b.stretch_rank);
  const [open, setOpen] = useState<string | null>(null);

  return (
    <ScannerInstrumentShell
      moduleId="BOUNCE / 09"
      title="Bounce Setup"
      titleAccessory={
        <InfoTooltip
          text={SECTION_HELP}
          ariaLabel="Bounce Setup scanner details"
          triggerTestId="bounce-setup-title-tooltip"
          contentTestId="bounce-setup-title-tooltip-content"
        />
      }
      controls={
        <div className="strength-confirmation__meta">
          {lastSync && <span className="report-meta">{new Date(lastSync).toLocaleTimeString()}</span>}
          <span className="pill defined">{data?.bounce_count ?? 0} SETUPS</span>
          <span className="pill neutral">{data?.coverage?.ranked ?? 0} RANKED</span>
          {onScan && (
            <button type="button" className="theta-scan-button strength-scan-button" onClick={onScan} disabled={scanning}>
              <Loader2 size={12} className={scanning ? "spin" : ""} />
              {scanning ? "SCANNING" : "SCAN"}
            </button>
          )}
        </div>
      }
      rail={[
        { k: "universe", v: data?.universe ?? "---" },
        { k: "window", v: data?.window ? `${data.window} sessions` : "---" },
        { k: "as.of", v: data?.as_of ?? "---" },
        { k: "setups", v: String(data?.bounce_count ?? 0) },
      ]}
      className="strength-confirmation"
      testId="bounce-setup-section"
    >
      {error && <RequestError error={error} onRetry={scanning ? undefined : onRetry} retainedData={rows.length > 0} />}
      {loading && rows.length === 0 ? (
        <div className="section-body">
          <div className="snapshot-card__empty">Ranking downside stretch, fixed-strike vol and skew...</div>
        </div>
      ) : rows.length === 0 ? (
        <div className="section-body" data-testid="bounce-setup-empty">
          <SectionEmptyState
            icon={Activity}
            headline="No bounce setup readings"
            secondary="No scan has been written yet. Run SCAN to rank the universe."
          />
        </div>
      ) : (
        <div className="section-body table-wrap strength-confirmation__table-wrap">
          <table>
            <thead>
              <tr>
                <th>Ticker</th>
                <th>Verdict</th>
                <th className="right">Stretch rank</th>
                <th className="right">RSI</th>
                <th className="right">20d return</th>
                <th className="right">FS vol off peak</th>
                <th className="right">Skew off max</th>
                <th>Contract</th>
                <th>Flow</th>
                <th>Action</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => {
                const t = row.ticker.toUpperCase();
                const expanded = open === t;
                return (
                  <Fragment key={`bounce-${t}`}>
                    <tr
                      data-testid={`bounce-row-${t}`}
                      onClick={() => setOpen(expanded ? null : t)}
                      aria-expanded={expanded}
                      style={{ cursor: "pointer" }}
                    >
                      <td onClick={(e) => e.stopPropagation()}><TickerLink ticker={t} /></td>
                      <td><span className={`theta-pill theta-pill--${verdictTone(row.verdict)}`}>{bounceVerdictLabel(row.verdict)}</span></td>
                      <td className="right mono" data-testid="bounce-stretch-rank">{row.stretch_rank}</td>
                      <td className="right mono">{fmt(row.rsi)}</td>
                      <td className="right mono" data-testid="bounce-ret20">{fmtPct(row.ret_20d)}</td>
                      <td className="right mono">{fmt(row.vol?.off_peak)}</td>
                      <td className="right mono">{fmt(row.skew?.ease, 2)}</td>
                      <td className="mono">{row.contract?.symbol ?? "---"}</td>
                      <td className="mono">{flowLabel(row)}</td>
                      <td><ActionCell row={row} /></td>
                    </tr>
                    {expanded && (
                      <tr>
                        <td colSpan={COLS}>
                          <BounceDetailChart ticker={t} series={row.series ?? []} />
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </ScannerInstrumentShell>
  );
}
