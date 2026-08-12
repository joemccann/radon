"use client";

import { useState } from "react";
import Link from "next/link";

import { useThetaHarvester } from "@/lib/useThetaHarvester";
import { useStrengthConfirmation } from "@/lib/useStrengthConfirmation";
import {
  formatScanSample,
  gateCells,
  scoreTone,
  strengthBadge,
  thetaStructLabel,
} from "@/lib/scannerHero";
import { fmtMoney } from "@/lib/format/money";

const TOP_N = 4;

type Tab = "theta" | "strength";

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
  const strength = useStrengthConfirmation(tab === "strength");

  const active = tab === "theta" ? theta : strength;
  const scanTime = active.data?.scan_time ?? null;
  const scanned = active.data?.tickers_scanned ?? null;
  const candidates = active.data?.candidates_found ?? null;
  const shown =
    tab === "theta"
      ? Math.min(TOP_N, theta.data?.results?.length ?? 0)
      : Math.min(TOP_N, strength.data?.results?.length ?? 0);

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
            className={`signals-hero__tab${tab === "strength" ? " is-active" : ""}`}
            onClick={() => setTab("strength")}
            aria-pressed={tab === "strength"}
          >
            7-Step Strength
          </button>
        </div>
      </header>

      {tab === "theta" ? (
        theta.loading && !theta.data ? (
          <div className="news-feed-empty">Loading theta harvester…</div>
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
      ) : strength.loading && !strength.data ? (
        <div className="news-feed-empty">Loading strength confirmation…</div>
      ) : !strength.data?.results?.length ? (
        <div className="news-feed-empty">No strength candidates in the last scan.</div>
      ) : (
        <div className="signals-hero__table" role="table" aria-label="Strength confirmation candidates">
          <div className="signals-hero__row signals-hero__row--head signals-hero__row--strength" role="row">
            <span>#</span>
            <span>Score</span>
            <span>Ticker · Spot</span>
            <span>Gate Ladder</span>
            <span className="num">Passed</span>
            <span className="num">State</span>
          </div>
          {strength.data.results.slice(0, TOP_N).map((row, i) => {
            const badge = strengthBadge(row.verdict);
            return (
              <Link
                key={row.ticker}
                href={`/${row.ticker}`}
                className="signals-hero__row signals-hero__row--strength"
                role="row"
              >
                <span className="signals-hero__rank">{rank(i)}</span>
                <span className="signals-hero__score">
                  <span className="signals-hero__score-value">{row.score}</span>
                  <span className="signals-hero__score-bar">
                    <span
                      className={`signals-hero__score-fill signals-hero__score-fill--${scoreTone(row.score)}`}
                      style={{ width: `${Math.max(0, Math.min(100, row.score)).toFixed(0)}%` }}
                    />
                  </span>
                </span>
                <span className="signals-hero__ticker-cell">
                  <span className="signals-hero__ticker">{row.ticker}</span>
                  <span className="signals-hero__struct">{fmtMoney(row.spot)}</span>
                </span>
                <span className="gate-ladder" aria-label={`${row.groups_passed} of 7 gates passed`}>
                  {gateCells(row).map((cell) => (
                    <span
                      key={cell.code}
                      title={cell.title}
                      className={`gate-ladder__cell${cell.passed === false ? " gate-ladder__cell--fail" : ""}${cell.passed == null ? " gate-ladder__cell--unknown" : ""}`}
                    >
                      {cell.code}
                    </span>
                  ))}
                </span>
                <span className="num signals-hero__passed">
                  <span className={`signals-hero__passed-count signals-hero__passed-count--${scoreTone(row.score)}`}>
                    {row.groups_passed}
                  </span>
                  <span className="signals-hero__passed-total">/7</span>
                </span>
                <span className="num">
                  <span className={`signals-hero__badge signals-hero__badge--${badge.tone}`}>{badge.label}</span>
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
