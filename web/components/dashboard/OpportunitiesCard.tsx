"use client";

import { useState } from "react";
import Link from "next/link";
import { useScanner } from "@/lib/useScanner";
import { useDiscover } from "@/lib/useDiscover";
import { useLeap } from "@/lib/useLeap";
import { useGarchConvergence } from "@/lib/useGarchConvergence";
import { useThetaHarvester } from "@/lib/useThetaHarvester";
import { formatForecastBand, forecastBandTone } from "@/lib/forecastBand";

type Tab = "scanner" | "discover" | "theta" | "leap" | "garch";

async function triggerLeapScan(): Promise<void> {
  const res = await fetch("/api/leap/scan", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ preset: "mag7" }),
    cache: "no-store",
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(text || `LEAP scan failed (${res.status})`);
  }
}

async function triggerGarchScan(): Promise<void> {
  const res = await fetch("/api/garch-convergence/scan", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ preset: "mega-tech" }),
    cache: "no-store",
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(text || `GARCH scan failed (${res.status})`);
  }
}

async function triggerThetaScan(): Promise<void> {
  const res = await fetch("/api/scanner/theta/scan", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ preset: "ndx100" }),
    cache: "no-store",
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(text || `Theta scan failed (${res.status})`);
  }
}

/**
 * OpportunitiesCard — surfaces trading candidates from the scanner, the
 * discover (dark-pool) engine, the LEAP IV-mispricing scan, and the
 * GARCH cross-asset vol repricing scan. Single card with a tab toggle
 * so the dashboard doesn't burn vertical space on four parallel lists.
 * Top 5 candidates each.
 */
export function OpportunitiesCard() {
  const [tab, setTab] = useState<Tab>("scanner");
  const [leapScanning, setLeapScanning] = useState(false);
  const [leapError, setLeapError] = useState<string | null>(null);
  const [thetaScanning, setThetaScanning] = useState(false);
  const [thetaError, setThetaError] = useState<string | null>(null);
  const [garchScanning, setGarchScanning] = useState(false);
  const [garchError, setGarchError] = useState<string | null>(null);

  const scanner = useScanner(tab === "scanner");
  const discover = useDiscover(tab === "discover");
  const theta = useThetaHarvester(tab === "theta");
  const leap = useLeap(tab === "leap");
  const garch = useGarchConvergence(tab === "garch");

  const scannerRows = (scanner.data?.top_signals ?? []).slice(0, 5);
  const discoverRows = (discover.data?.candidates ?? []).slice(0, 5);
  // LEAP results are pre-sorted by best_gap desc inside the script.
  const leapRows = (leap.data?.results ?? []).slice(0, 5);
  const thetaRows = (theta.data?.results ?? []).slice(0, 5);
  // GARCH: rank actionable pairs by |divergence| descending; if none pass
  // gates, show the highest-divergence pairs anyway so the user sees why.
  const garchRows = (garch.data?.pairs ?? [])
    .slice()
    .sort((a, b) => Math.abs(b.divergence) - Math.abs(a.divergence))
    .slice(0, 5);

  const loading =
    tab === "scanner"
      ? scanner.loading
      : tab === "discover"
        ? discover.loading
        : tab === "theta"
          ? theta.loading || thetaScanning
          : tab === "leap"
            ? leap.loading || leapScanning
            : garch.loading || garchScanning;
  const error =
    tab === "scanner"
      ? scanner.error
      : tab === "discover"
        ? discover.error
        : tab === "theta"
          ? thetaError || theta.error
          : tab === "leap"
            ? leapError || leap.error
            : garchError || garch.error;
  const lastSync =
    tab === "scanner"
      ? scanner.lastSync
      : tab === "discover"
        ? discover.lastSync
        : tab === "theta"
          ? theta.lastSync
          : tab === "leap"
            ? leap.lastSync
            : garch.lastSync;

  const onLeapRefresh = async () => {
    if (leapScanning) return;
    setLeapError(null);
    setLeapScanning(true);
    try {
      await triggerLeapScan();
      leap.syncNow();
    } catch (err) {
      setLeapError(err instanceof Error ? err.message : "LEAP scan failed");
    } finally {
      setLeapScanning(false);
    }
  };

  const onGarchRefresh = async () => {
    if (garchScanning) return;
    setGarchError(null);
    setGarchScanning(true);
    try {
      await triggerGarchScan();
      garch.syncNow();
    } catch (err) {
      setGarchError(err instanceof Error ? err.message : "GARCH scan failed");
    } finally {
      setGarchScanning(false);
    }
  };

  const onThetaRefresh = async () => {
    if (thetaScanning) return;
    setThetaError(null);
    setThetaScanning(true);
    try {
      await triggerThetaScan();
      theta.syncNow();
    } catch (err) {
      setThetaError(err instanceof Error ? err.message : "Theta scan failed");
    } finally {
      setThetaScanning(false);
    }
  };

  return (
    <section className="snapshot-card">
      <span className="panel-edge-trace" aria-hidden />
      <header className="snapshot-card__header">
        <p className="panel-eyebrow">Opportunities / 04</p>
        <h3 className="panel-title">Trading Candidates</h3>
        {tab === "theta" ? (
          <button
            type="button"
            className="snapshot-card__see-all snapshot-card__see-all--action"
            onClick={onThetaRefresh}
            disabled={thetaScanning}
          >
            {thetaScanning ? "Scanning…" : "Run latest →"}
          </button>
        ) : tab === "leap" ? (
          <button
            type="button"
            className="snapshot-card__see-all snapshot-card__see-all--action"
            onClick={onLeapRefresh}
            disabled={leapScanning}
          >
            {leapScanning ? "Scanning…" : "Run latest →"}
          </button>
        ) : tab === "garch" ? (
          <button
            type="button"
            className="snapshot-card__see-all snapshot-card__see-all--action"
            onClick={onGarchRefresh}
            disabled={garchScanning}
          >
            {garchScanning ? "Scanning…" : "Run latest →"}
          </button>
        ) : (
          <Link
            className="snapshot-card__see-all"
            href={tab === "scanner" ? "/scanner" : "/discover"}
          >
            {tab === "scanner" ? "All scanner →" : "All discover →"}
          </Link>
        )}
      </header>

      <div className="snapshot-tabs" role="tablist">
        <button
          type="button"
          role="tab"
          aria-selected={tab === "scanner"}
          className={`snapshot-tab${tab === "scanner" ? " snapshot-tab--active" : ""}`}
          onClick={() => setTab("scanner")}
        >
          Scanner
          {scanner.data ? <span className="snapshot-tab__count">{scanner.data.signals_found}</span> : null}
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === "discover"}
          className={`snapshot-tab${tab === "discover" ? " snapshot-tab--active" : ""}`}
          onClick={() => setTab("discover")}
        >
          Discover
          {discover.data ? <span className="snapshot-tab__count">{discover.data.candidates_found}</span> : null}
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === "leap"}
          className={`snapshot-tab${tab === "leap" ? " snapshot-tab--active" : ""}`}
          onClick={() => setTab("leap")}
        >
          LEAP
          {leap.data ? <span className="snapshot-tab__count">{leap.data.results.length}</span> : null}
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === "theta"}
          className={`snapshot-tab${tab === "theta" ? " snapshot-tab--active" : ""}`}
          onClick={() => setTab("theta")}
        >
          Theta
          {theta.data ? <span className="snapshot-tab__count">{theta.data.theta_harvest_count}</span> : null}
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === "garch"}
          className={`snapshot-tab${tab === "garch" ? " snapshot-tab--active" : ""}`}
          onClick={() => setTab("garch")}
        >
          GARCH
          {garch.data ? <span className="snapshot-tab__count">{garch.data.pairs.length}</span> : null}
        </button>
        <span className="snapshot-tabs__meta">
          {lastSync ? `Last sample ${new Date(lastSync).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false })}` : "—"}
        </span>
      </div>

      {loading ? (
        <div className="snapshot-card__empty">Sampling…</div>
      ) : error ? (
        <div className="snapshot-card__error">{error}</div>
      ) : tab === "scanner" ? (
        scannerRows.length === 0 ? (
          <div className="snapshot-card__empty">No scanner signals captured yet.</div>
        ) : (
          <ul className="snapshot-rows">
            {scannerRows.map((s) => (
              <li key={`s-${s.ticker}`} className="snapshot-row">
                <Link href={`/${encodeURIComponent(s.ticker)}`} className="snapshot-row__ticker">
                  {s.ticker}
                </Link>
                <span className="snapshot-row__signal">
                  {s.signal}
                  {s.forecast ? (
                    <span
                      className={`snapshot-row__forecast snapshot-row__forecast--${forecastBandTone(s.forecast)}`}
                      title={`Chronos-2 ${s.forecast.horizon}d flow band${s.forecast.convex_reinforced ? " · convex upside (Gate 1)" : ""}`}
                    >
                      {" "}fc {formatForecastBand(s.forecast)}
                    </span>
                  ) : null}
                </span>
                <span className={`snapshot-row__direction snapshot-row__direction--${s.direction.toLowerCase()}`}>
                  {s.direction}
                </span>
                <span className="snapshot-row__score">{s.score.toFixed(1)}</span>
              </li>
            ))}
          </ul>
        )
      ) : tab === "discover" ? (
        discoverRows.length === 0 ? (
          <div className="snapshot-card__empty">No discover candidates captured yet.</div>
        ) : (
          <ul className="snapshot-rows">
            {discoverRows.map((c) => (
              <li key={`d-${c.ticker}`} className="snapshot-row">
                <Link href={`/${encodeURIComponent(c.ticker)}`} className="snapshot-row__ticker">
                  {c.ticker}
                </Link>
                <span className="snapshot-row__signal">
                  {c.options_bias?.toUpperCase() ?? "—"} · {c.dp_direction || "DP"} {c.dp_strength?.toFixed(1) ?? ""}
                </span>
                <span className={`snapshot-row__direction snapshot-row__direction--${(c.dp_direction || "").toLowerCase()}`}>
                  {c.alerts ?? 0} alerts
                </span>
                <span className="snapshot-row__score">{c.score?.toFixed(1) ?? "—"}</span>
              </li>
            ))}
          </ul>
        )
      ) : tab === "theta" ? (
        thetaRows.length === 0 ? (
          <div className="snapshot-card__empty">
            No theta harvester scans on file. Click <strong>Run latest →</strong> above, or open Scanner.
          </div>
        ) : (
          <ul className="snapshot-rows">
            {thetaRows.map((r) => {
              const verdict =
                r.verdict === "THETA_HARVEST"
                  ? "TRUE THETA"
                  : r.verdict === "DIRECTIONAL_DISGUISE"
                    ? "DIRECTIONAL"
                    : "WATCH";
              const direction =
                r.verdict === "THETA_HARVEST"
                  ? "bull"
                  : r.verdict === "DIRECTIONAL_DISGUISE"
                    ? "bear"
                    : "neutral";
              const edgeText = r.iv_rv_edge >= 0
                ? `+${r.iv_rv_edge.toFixed(1)}`
                : r.iv_rv_edge.toFixed(1);
              return (
                <li key={`t-${r.ticker}`} className="snapshot-row">
                  <Link href={`/${encodeURIComponent(r.ticker)}?tab=chain`} className="snapshot-row__ticker">
                    {r.ticker}
                  </Link>
                  <span className="snapshot-row__signal">
                    {r.structure.short_put.strike.toFixed(0)}P/{r.structure.short_call.strike.toFixed(0)}C · Δ {(r.structure.net_delta * 100).toFixed(1)}
                  </span>
                  <span className={`snapshot-row__direction snapshot-row__direction--${direction}`}>
                    {verdict}
                  </span>
                  <span className="snapshot-row__score">{edgeText}</span>
                </li>
              );
            })}
          </ul>
        )
      ) : tab === "leap" ? (
        leapRows.length === 0 ? (
          <div className="snapshot-card__empty">
            No LEAP scans on file. Click <strong>Run latest →</strong> above, or wait for the next scheduled scan.
          </div>
        ) : (
          <ul className="snapshot-rows">
            {leapRows.map((r) => (
              <li key={`l-${r.ticker}`} className="snapshot-row">
                <Link href={`/${encodeURIComponent(r.ticker)}`} className="snapshot-row__ticker">
                  {r.ticker}
                </Link>
                <span className="snapshot-row__signal">
                  {r.current_iv != null ? `IV ${r.current_iv.toFixed(1)}` : "IV —"}
                  {" · "}
                  {r.hv_20 != null ? `HV20 ${r.hv_20.toFixed(1)}` : "HV20 —"}
                </span>
                <span
                  className={`snapshot-row__direction snapshot-row__direction--${r.is_mispriced ? "bull" : "neutral"}`}
                >
                  {r.is_mispriced ? "Mispriced" : "—"}
                </span>
                <span className="snapshot-row__score">
                  {r.best_gap >= 0 ? `+${r.best_gap.toFixed(1)}` : r.best_gap.toFixed(1)}
                </span>
              </li>
            ))}
          </ul>
        )
      ) : garchRows.length === 0 ? (
        <div className="snapshot-card__empty">
          No GARCH scans on file. Click <strong>Run latest →</strong> above, or wait for the next scheduled scan.
        </div>
      ) : (
        <ul className="snapshot-rows">
          {garchRows.map((p) => {
            // Signal values: STRONG / MODERATE / WEAK / NONE.
            // Color by gates_passed: bull (signal-core) when actionable,
            // muted otherwise. Always render the signal label so the user
            // sees WHY a row is muted (NONE / failing gate) instead of a
            // dead "—" placeholder.
            const direction = p.gates_passed ? "bull" : "neutral";
            const signalLabel = p.signal || "NONE";
            const pairKey = `g-${p.pair[0]}-${p.pair[1]}`;
            const gapText = p.lagger_hv_iv_gap >= 0
              ? `+${p.lagger_hv_iv_gap.toFixed(1)}`
              : p.lagger_hv_iv_gap.toFixed(1);
            const divergenceText = p.divergence >= 0
              ? `+${p.divergence.toFixed(2)}`
              : p.divergence.toFixed(2);
            return (
              <li key={pairKey} className="snapshot-row">
                <Link
                  href={`/${encodeURIComponent(p.lagger)}`}
                  className="snapshot-row__ticker"
                  title={`Lagger ${p.lagger}, led by ${p.leader}`}
                >
                  {p.pair[0]}↔{p.pair[1]}
                </Link>
                <span className="snapshot-row__signal">
                  Lag {p.lagger} · gap {gapText}
                </span>
                <span className={`snapshot-row__direction snapshot-row__direction--${direction}`}>
                  {signalLabel}
                </span>
                <span className="snapshot-row__score">{divergenceText}</span>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
