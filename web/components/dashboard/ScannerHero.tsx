"use client";

import { useState } from "react";
import Link from "next/link";

import { useThetaHarvester } from "@/lib/useThetaHarvester";
import { useVolCone } from "@/lib/useVolCone";
import {
  coneFillPct,
  formatScanSample,
  thetaStructLabel,
  volConeExpiryLabel,
  volConeTone,
} from "@/lib/scannerHero";
import {
  formatIvPct,
  formatPercentile,
  formatVolConeRegime,
  volConeOrderHref,
  volConeTradeAriaLabel,
} from "@/lib/volCone";
import { fmtMoney } from "@/lib/format/money";

const TOP_N = 4;

type Tab = "theta" | "cone";

function rank(i: number): string {
  return String(i + 1).padStart(2, "0");
}

/**
 * ScannerHero — the dashboard's ranked-candidate table. Two scans share the
 * slot behind pill tabs; only the active tab's hook polls. The full scanner
 * matrix (flow / discover / LEAP / GARCH) stays on /scanner.
 */
export default function ScannerHero() {
  const [tab, setTab] = useState<Tab>("theta");
  const theta = useThetaHarvester(tab === "theta");
  const cone = useVolCone(tab === "cone");

  // The vol-cone payload counts names scanned and cheap-cone hits rather than
  // the scanner-shaped tickers_scanned / candidates_found.
  const scanTime = (tab === "theta" ? theta.data?.scan_time : cone.data?.scan_time) ?? null;
  const scanned = tab === "theta" ? theta.data?.tickers_scanned ?? null : cone.data?.count ?? null;
  const candidates =
    tab === "theta" ? theta.data?.candidates_found ?? null : cone.data?.hit_count ?? null;
  const shown =
    tab === "theta"
      ? Math.min(TOP_N, theta.data?.results?.length ?? 0)
      : Math.min(TOP_N, cone.data?.hits?.length ?? 0);

  return (
    <section className="signals-hero snapshot-card">
      <header className="signals-hero__header">
        <div>
          <p className="panel-eyebrow">Signals / 02</p>
          <h3 className="panel-title">Top candidates</h3>
        </div>
        <div className="signals-hero__tabs" role="tablist" aria-label="Scan selector">
          <button
            type="button"
            className={`signals-hero__tab${tab === "theta" ? " is-active" : ""}`}
            onClick={() => setTab("theta")}
            aria-pressed={tab === "theta"}
          >
            Theta Harvester
          </button>
          <button
            type="button"
            className={`signals-hero__tab${tab === "cone" ? " is-active" : ""}`}
            onClick={() => setTab("cone")}
            aria-pressed={tab === "cone"}
          >
            Vol Cone
          </button>
        </div>
      </header>

      {tab === "theta" ? (
        theta.loading && !theta.data ? (
          <div className="news-feed-empty">Loading theta harvester…</div>
        ) : theta.error ? (
          <div className="news-feed-error" role="alert">{theta.error}</div>
        ) : !theta.data?.results?.length ? (
          <div className="news-feed-empty">No theta candidates in the last scan.</div>
        ) : (
          <div className="signals-hero__table" role="table" aria-label="Theta harvester candidates">
            <div className="signals-hero__row signals-hero__row--head signals-hero__row--theta" role="row">
              <span>#</span>
              <span>Score</span>
              <span>Ticker · Struct.</span>
              <span className="num">Theta/Day</span>
              <span className="num">Credit</span>
              <span className="num">DTE</span>
            </div>
            {theta.data.results.slice(0, TOP_N).map((row, i) => (
              <Link
                key={row.ticker}
                href={`/${row.ticker}`}
                className="signals-hero__row signals-hero__row--theta"
                role="row"
              >
                <span className="signals-hero__rank">{rank(i)}</span>
                <span className="signals-hero__score">
                  <span className="signals-hero__score-value">{row.score.toFixed(1)}</span>
                  <span className="signals-hero__score-bar">
                    <span
                      className="signals-hero__score-fill signals-hero__score-fill--strong"
                      style={{ width: `${Math.max(0, Math.min(100, row.score)).toFixed(0)}%` }}
                    />
                  </span>
                </span>
                <span className="signals-hero__ticker-cell">
                  <span className="signals-hero__ticker">{row.ticker}</span>
                  <span className="signals-hero__struct">{thetaStructLabel(row)}</span>
                </span>
                <span className="num signals-hero__theta">
                  {row.structure?.theta != null ? `+${row.structure.theta.toFixed(2)}/d` : "—"}
                </span>
                <span className="num">
                  {row.structure?.credit != null ? `$${row.structure.credit.toFixed(2)}` : "—"}
                </span>
                <span className="num signals-hero__dte">{row.structure?.dte ?? "—"}</span>
              </Link>
            ))}
          </div>
        )
      ) : cone.loading && !cone.data ? (
        <div className="news-feed-empty">Loading vol cone…</div>
      ) : cone.error ? (
        <div className="news-feed-error" role="alert">{cone.error}</div>
      ) : !cone.data?.hits?.length ? (
        <div className="news-feed-empty">No cheap vol cones in the last scan.</div>
      ) : (
        <div className="signals-hero__table" role="table" aria-label="Vol cone candidates">
          <div className="signals-hero__row signals-hero__row--head signals-hero__row--cone" role="row">
            <span>#</span>
            <span>ATM IV</span>
            <span>Ticker · Spot · Expiry</span>
            <span className="num">Wing %ile</span>
            <span className="num">Regime</span>
          </div>
          {cone.data.hits.slice(0, TOP_N).map((row, i) => {
            const tone = volConeTone(row.regime);
            const ariaLabel = volConeTradeAriaLabel(row);
            return (
              <Link
                key={`${row.ticker}:${row.expiry}`}
                href={volConeOrderHref(row) ?? `/${row.ticker}`}
                className="signals-hero__row signals-hero__row--cone"
                role="row"
                aria-label={ariaLabel ?? undefined}
              >
                <span className="signals-hero__rank">{rank(i)}</span>
                <span className="signals-hero__score">
                  <span className="signals-hero__score-value">{formatIvPct(row.atm_iv)}</span>
                  {/* Bar reads like the other tabs: longer is better, and on a
                      cone cheaper is better, so the p10 floor fills it. */}
                  <span
                    className="signals-hero__score-bar"
                    title={`ATM IV vs this expiry's 90/10 cone · ${formatPercentile(row.atm_percentile)} of sessions cheaper`}
                  >
                    <span
                      className={`signals-hero__score-fill signals-hero__score-fill--${tone}`}
                      style={{ width: `${coneFillPct(row).toFixed(0)}%` }}
                    />
                  </span>
                </span>
                <span className="signals-hero__ticker-cell">
                  <span className="signals-hero__ticker">{row.ticker}</span>
                  <span
                    className="signals-hero__struct"
                    title={volConeExpiryLabel(row)}
                  >{`${fmtMoney(row.spot)} · ${volConeExpiryLabel(row)}`}</span>
                </span>
                <span className="num signals-hero__dte">{formatPercentile(row.wing_score)}</span>
                <span className="num">
                  <span className={`signals-hero__badge signals-hero__badge--${tone}`}>
                    {formatVolConeRegime(row.regime)}
                  </span>
                </span>
              </Link>
            );
          })}
        </div>
      )}

      <footer className="panel-meta-rail" aria-label="Scan calibration">
        <div className="panel-meta-rail-item">
          <span className="k">scanned</span>
          <span className="v">{scanned ?? "—"}</span>
        </div>
        <div className="panel-meta-rail-item">
          <span className="k">candidates</span>
          <span className="v">{candidates ?? "—"}</span>
        </div>
        <div className="panel-meta-rail-item">
          <span className="k">shown</span>
          <span className="v">{shown || "—"}</span>
        </div>
        <div className="panel-meta-rail-item">
          <span className="k">sample</span>
          <span className="v">{formatScanSample(scanTime)}</span>
        </div>
        <Link className="panel-meta-rail-item signals-hero__open-scanner" href="/scanner">
          OPEN SCANNER →
        </Link>
      </footer>
    </section>
  );
}
