"use client";

import { useMemo } from "react";
import { Activity } from "lucide-react";
import ShareReportModal from "./ShareReportModal";
import { useRegime } from "@/lib/useRegime";
import { useMenthorqCta } from "@/lib/useMenthorqCta";
import { SECTION_TOOLTIPS } from "@/lib/sectionTooltips";
import InfoTooltip from "./InfoTooltip";
import SortableCtaTable, { type CtaSectionCallout } from "./SortableCtaTable";
import CtaBriefing from "./CtaBriefing";
import { formatCtaPercentileLabel, normalizeCtaPercentile } from "@/lib/ctaPercentiles";
import {
  CTA_AUM_BN,
  CTA_VOL_TARGET,
  dayOverDayEstSellingDelta,
  formatCtaModelInputsStamp,
  priorSessionEstSellingBn,
} from "@/lib/ctaVolTarget";

/* ─── Helpers ────────────────────────────────────────── */

function fmt(v: number | null | undefined, decimals = 2): string {
  if (v == null || !Number.isFinite(v)) return "---";
  return v.toFixed(decimals);
}

function formatFetchedAt(iso: string | null | undefined): string {
  if (!iso) return "";
  try {
    const d = new Date(iso);
    return (
      d.toLocaleString("en-US", {
        timeZone: "America/New_York",
        month: "short",
        day: "numeric",
        hour: "numeric",
        minute: "2-digit",
        hour12: true,
      }) + " ET"
    );
  } catch {
    return "";
  }
}

function formatSyncStamp(iso: string | null | undefined): string {
  return formatFetchedAt(iso);
}

function normalizeSyncState(value: string | null | undefined): string {
  if (!value) return "unknown";
  if (value === "success") return "healthy";
  if (value === "error") return "degraded";
  return value;
}

function pctile(value: number | null | undefined): number | null {
  return normalizeCtaPercentile(value);
}

/* ─── CtaPage ────────────────────────────────────────── */

export default function CtaPage() {
  const { data } = useRegime(false);
  const { data: ctaData, loading, error } = useMenthorqCta();

  const cta = data?.cta ?? null;
  const exposurePct = cta?.exposure_pct ?? null;
  const realizedVol = cta?.realized_vol ?? data?.realized_vol ?? null;
  const estSellingBn = cta?.est_selling_bn ?? null;

  const dayChangeBn = useMemo(() => {
    const priorBn = priorSessionEstSellingBn(data?.history, data?.date);
    return dayOverDayEstSellingDelta(estSellingBn, priorBn);
  }, [data?.history, data?.date, estSellingBn]);

  const asOfLabel = formatFetchedAt(data?.scan_time);
  const modelInputsStamp = formatCtaModelInputsStamp({
    realizedVol,
    volTarget: CTA_VOL_TARGET,
    aumBn: CTA_AUM_BN,
    asOfLabel: asOfLabel || null,
  });

  const order = ["main", "index", "commodity", "currency"] as const;

  /* ─── Per-section callouts ─────────────────────────── */
  function buildCallout(key: typeof order[number], tables: NonNullable<typeof ctaData>["tables"]): CtaSectionCallout | undefined {
    if (!tables) return undefined;
    const rows = tables[key];
    if (!rows || rows.length === 0) return undefined;

    switch (key) {
      case "main": {
        const spx = rows.find(r => r.underlying.toLowerCase().includes("s&p") || r.underlying.toLowerCase().includes("e-mini"));
        const bonds = rows.filter(r => r.underlying.toLowerCase().includes("t-note") || r.underlying.toLowerCase().includes("treasury"));
        if (spx && (pctile(spx.percentile_3m) ?? 101) <= 10) {
          const flipped = spx.position_1m_ago > 0 && spx.position_today < 0;
          const bondShortCount = bonds.filter(b => (pctile(b.percentile_3m) ?? 101) <= 10).length;
          return {
            kind: "short",
            headline: `MAX SHORT · ${formatCtaPercentileLabel(spx.percentile_3m)} pctile (3M), z ${spx.z_score_3m.toFixed(2)}.`,
            body: [
              flipped ? `Flipped from ${spx.position_1m_ago.toFixed(2)} long one month ago.` : null,
              bondShortCount >= 2 ? `${bondShortCount} bond contracts at 0th pctile — full duration short.` : null,
              "Violent short-covering likely on any bullish catalyst.",
            ].filter(Boolean).join(" "),
          };
        }
        if (spx && (pctile(spx.percentile_3m) ?? -1) >= 85) {
          return { kind: "long", headline: `HEAVY LONG · ${formatCtaPercentileLabel(spx.percentile_3m)} pctile.`, body: "CTA equity exposure elevated. Watch for mean reversion on disappointing macro." };
        }
        return undefined;
      }
      case "index": {
        const extreme = rows.filter(r => (pctile(r.percentile_3m) ?? 101) <= 5 && r.position_today < 0);
        if (extreme.length >= 3) {
          return {
            kind: "short",
            headline: `${extreme.length} INDEX FUTURES AT 0–5th PCTILE.`,
            body: "Global equity CTA positioning uniformly short. Cross-asset squeeze risk if risk sentiment turns.",
          };
        }
        return undefined;
      }
      case "commodity": {
        const crowdedLongs = rows
          .filter(r => (pctile(r.percentile_3m) ?? -1) >= 85 && r.position_today > 0)
          .sort((a, b) => (pctile(b.percentile_3m) ?? -1) - (pctile(a.percentile_3m) ?? -1));
        if (crowdedLongs.length >= 2) {
          const labels = crowdedLongs.slice(0, 3).map(r => `${r.underlying.split(" ")[0]} ${formatCtaPercentileLabel(r.percentile_3m)}`).join(", ");
          return {
            kind: "long",
            headline: `CROWDED LONGS · ${labels}.`,
            body: "Mean reversion risk elevated on energy and soft commodities. Stagflation trade at historically extreme levels.",
          };
        }
        const gold = rows.find(r => r.underlying.toLowerCase().includes("gold"));
        if (gold && (pctile(gold.percentile_3m) ?? 101) <= 10 && gold.position_today > 0 && gold.position_yesterday > gold.position_today) {
          return { kind: "neutral", headline: "GOLD REDUCING.", body: `Position softening (${gold.position_yesterday.toFixed(2)} to ${gold.position_today.toFixed(2)}) despite elevated spot price. CTA reduction signal.` };
        }
        return undefined;
      }
      case "currency": {
        const dxy = rows.find(r => r.underlying.toLowerCase().includes("dollar"));
        const shorts = rows
          .filter(r => r.position_today < 0 && ((pctile(r.percentile_3m) ?? 101) <= 20 || r.z_score_3m <= -1))
          .sort((a, b) => (pctile(a.percentile_3m) ?? 101) - (pctile(b.percentile_3m) ?? 101));
        const longs = rows
          .filter(r => r.position_today > 0 && ((pctile(r.percentile_3m) ?? -1) >= 80 || r.z_score_3m >= 1))
          .sort((a, b) => (pctile(b.percentile_3m) ?? -1) - (pctile(a.percentile_3m) ?? -1));
        if (dxy && (pctile(dxy.percentile_3m) ?? -1) >= 85 && shorts.length >= 2) {
          return {
            kind: "long",
            headline: `LONG USD · ${formatCtaPercentileLabel(dxy.percentile_3m)} pctile.`,
            body: `${shorts.length} currency pairs short at extreme levels. Dollar strength trade crowded — watch for reversal if risk appetite returns.`,
          };
        }
        if (shorts.length > 0 && longs.length > 0) {
          return {
            kind: "neutral",
            headline: "FX DISPERSION.",
            body: `${shorts[0].underlying} at ${formatCtaPercentileLabel(shorts[0].percentile_3m)} pctile short while ${longs[0].underlying} sits ${formatCtaPercentileLabel(longs[0].percentile_3m)} pctile long.`,
          };
        }
        if (shorts.length >= 2) {
          return {
            kind: "short",
            headline: "DEFENSIVE FX SKEW.",
            body: shorts.slice(0, 2).map(r => `${r.underlying} ${formatCtaPercentileLabel(r.percentile_3m)}`).join(", "),
          };
        }
        if (longs.length >= 2) {
          return {
            kind: "long",
            headline: "CROWDED FX LONGS.",
            body: longs.slice(0, 2).map(r => `${r.underlying} ${formatCtaPercentileLabel(r.percentile_3m)}`).join(", "),
          };
        }
        return undefined;
      }
    }
  }
  const fetchLabel = formatFetchedAt(ctaData?.fetched_at);
  const ctaCacheMeta = ctaData?.cache_meta ?? null;
  const syncHealth = ctaData?.sync_health ?? null;
  const syncStatus = ctaData?.sync_status ?? null;
  const activeSyncStatus = syncHealth ?? syncStatus;
  const syncState = normalizeSyncState(
    syncHealth?.state
    ?? syncStatus?.state
    ?? syncStatus?.status
    ?? null,
  );
  const ctaIsStale = Boolean(ctaCacheMeta?.is_stale);
  const staleTargetDate = ctaCacheMeta?.expected_date ?? ctaCacheMeta?.target_date ?? activeSyncStatus?.target_date ?? null;
  const staleCacheDate = ctaCacheMeta?.latest_available_date ?? ctaCacheMeta?.latest_cache_date ?? ctaData?.date ?? null;
  const staleCopy = !ctaData?.tables
    ? `CTA positioning is stale. Expected ${staleTargetDate ?? "---"}. No cached data is available yet.`
    : `CTA positioning is stale. Expected ${staleTargetDate ?? "---"}. Latest available ${staleCacheDate ?? "---"}.`;

  let syncDetail = "";
  const syncStartedAt = syncHealth?.last_attempt_started_at ?? syncStatus?.started_at ?? null;
  const syncErrorMessage = syncHealth?.last_error?.message
    ?? syncStatus?.last_error?.message
    ?? syncStatus?.error_excerpt
    ?? syncHealth?.message
    ?? syncStatus?.message
    ?? null;
  if (syncState === "syncing" || syncState === "running") {
    syncDetail = `Refresh in progress${syncStartedAt ? ` · STARTED ${formatSyncStamp(syncStartedAt)}` : ""}`;
  } else if (syncState === "degraded") {
    syncDetail = syncErrorMessage ?? "Last refresh attempt failed.";
  } else if (error) {
    syncDetail = error;
  }

  const statusBannerClass = syncState === "degraded" || error
    ? "cta-status-banner cta-status-banner-error"
    : syncState === "syncing" || syncState === "running"
      ? "cta-status-banner cta-status-banner-running"
      : "cta-status-banner";

  return (
    <>
    <div data-testid="cta-page" style={{ width: "100%", display: "flex", flexDirection: "column", gap: "0" }}>

      {/* ── Vol-Targeting Model Panel ─────────────────── */}
      <div data-testid="vol-targeting-model" style={{ padding: "16px", borderBottom: "1px solid var(--border)" }}>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: "6px",
            fontFamily: "var(--font-mono, monospace)",
            fontSize: "10px",
            fontWeight: 700,
            letterSpacing: "0.10em",
            color: "var(--text-muted)",
            textTransform: "uppercase",
            marginBottom: "6px",
          }}
        >
          <Activity size={14} />
          VOL-TARGETING MODEL
          <InfoTooltip text={SECTION_TOOLTIPS["VOL-TARGETING MODEL"]} />
        </div>
        <div
          data-testid="cta-model-inputs"
          style={{
            fontFamily: "var(--font-mono, monospace)",
            fontSize: "9px",
            fontWeight: 400,
            letterSpacing: "0.06em",
            color: "var(--text-muted)",
            textTransform: "uppercase",
            marginBottom: "12px",
          }}
        >
          {modelInputsStamp}
        </div>

        <div className="regime-cta-rows">
          <div className="regime-cta-row">
            <span>Implied Exposure</span>
            <span className={exposurePct != null && exposurePct < 50 ? "text-negative" : ""}>
              {fmt(exposurePct, 1)}%
            </span>
          </div>
          <div className="regime-cta-row">
            <span>Cut from 100%</span>
            <span
              className={
                cta?.forced_reduction_pct && cta.forced_reduction_pct > 0
                  ? "text-negative"
                  : "text-positive"
              }
            >
              {fmt(cta?.forced_reduction_pct, 1)}%
            </span>
          </div>
          <div className="regime-cta-row">
            <span>Implied cut vs AUM</span>
            <span
              data-testid="cta-implied-cut"
              className={
                estSellingBn != null && estSellingBn > 50 ? "text-negative" : ""
              }
            >
              ${fmt(estSellingBn, 1)}B
            </span>
          </div>
          <div className="regime-cta-row">
            <span>Day change</span>
            <span
              data-testid="cta-day-change"
              className={
                dayChangeBn == null
                  ? ""
                  : dayChangeBn > 0
                    ? "text-negative"
                    : dayChangeBn < 0
                      ? "text-positive"
                      : ""
              }
            >
              {dayChangeBn == null
                ? "---"
                : `${dayChangeBn > 0 ? "+" : dayChangeBn < 0 ? "-" : ""}$${Math.abs(dayChangeBn).toFixed(1)}B`}
            </span>
          </div>
        </div>

        {exposurePct != null && (
          <div className="regime-cta-gauge" style={{ marginTop: "12px" }}>
            <div className="regime-cta-gauge-label">EXPOSURE</div>
            <div className="regime-bar-track">
              <div
                className="regime-bar-fill"
                style={{
                  width: `${Math.min(exposurePct, 200) / 2}%`,
                  background:
                    exposurePct >= 80 ? "var(--positive)" : "var(--negative)",
                }}
              />
            </div>
            <div className="regime-cta-gauge-scale">
              <span>0%</span>
              <span>100%</span>
              <span>200%</span>
            </div>
          </div>
        )}
      </div>

      {/* ── MenthorQ CTA Positioning ──────────────────── */}
      <div style={{ width: "100%" }}>
        <div
          style={{
            fontFamily: "var(--font-mono, monospace)",
            fontSize: "10px",
            fontWeight: 700,
            letterSpacing: "0.10em",
            color: "var(--text-muted)",
            padding: "12px 12px 8px",
            borderBottom: "1px solid var(--border)",
            textTransform: "uppercase",
            display: "flex",
            alignItems: "center",
            gap: "8px",
          }}
        >
          <span style={{ flex: 1, display: "flex", alignItems: "baseline", gap: "8px" }}>
            MENTHORQ CTA POSITIONING — {ctaData?.date ?? "---"}{" "}
            <InfoTooltip text={SECTION_TOOLTIPS["MENTHORQ CTA POSITIONING"]} />
            {fetchLabel && (
              <span style={{ fontWeight: 400, fontSize: "9px", color: "var(--text-muted)", letterSpacing: "0.06em" }}>
                · FETCHED {fetchLabel}
              </span>
            )}
          </span>
          {ctaData?.tables && (
            <ShareReportModal
              modalTitle="CTA REPORT — SHARE TO X"
              shareEndpoint="/api/menthorq/cta/share"
              buttonTitle="Share CTA report to X"
              iconSize={11}
              shareContentTitle="CTA Share Preview"
            />
          )}
        </div>

        {!loading && ctaIsStale && (
          <div className={statusBannerClass} data-testid="cta-stale-banner" role="alert">
            <div className="cta-status-title">CTA CACHE STALE</div>
            <div className="cta-status-copy">{staleCopy}</div>
            {syncDetail && <div className="cta-status-meta">{syncDetail}</div>}
          </div>
        )}

        {loading && (
          <div
            className="cta-empty"
            style={{ padding: "24px 16px", fontFamily: "var(--font-mono, monospace)", fontSize: "11px", color: "var(--text-muted)" }}
          >
            Loading CTA positioning data...
          </div>
        )}

        {!loading && !ctaData?.tables && (
          <div
            className="cta-empty"
            style={{ padding: "24px 16px", fontFamily: "var(--font-mono, monospace)", fontSize: "11px", color: "var(--text-muted)" }}
          >
            No MenthorQ CTA data available. Run: <code>menthorq-cta</code>
          </div>
        )}

        {!loading && ctaData?.tables && (
          <div style={{ width: "100%", display: "flex", flexDirection: "column", gap: "0" }}>
            <CtaBriefing
              tables={ctaData.tables}
              estSellingBn={estSellingBn}
              dayChangeBn={dayChangeBn}
            />
            {order.map((key) => {
              const rows = ctaData.tables![key];
              if (!rows || rows.length === 0) return null;
              const callout = buildCallout(key, ctaData.tables!);
              return <SortableCtaTable key={key} sectionKey={key} rows={rows} callout={callout} />;
            })}
          </div>
        )}
      </div>
    </div>

    </>
  );
}
