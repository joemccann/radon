"use client";

import { Activity } from "lucide-react";
import { useRegime } from "@/lib/useRegime";
import { useMenthorqCta } from "@/lib/useMenthorqCta";
import { SECTION_TOOLTIPS } from "@/lib/sectionTooltips";
import InfoTooltip from "./InfoTooltip";
import SortableCtaTable from "./SortableCtaTable";

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

/* ─── CtaPage ────────────────────────────────────────── */

export default function CtaPage() {
  const { data } = useRegime(true);
  const { data: ctaData, loading, error } = useMenthorqCta();

  const cta = data?.cta ?? null;
  const exposurePct = cta?.exposure_pct ?? null;

  const order = ["main", "index", "commodity", "currency"] as const;
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
            marginBottom: "12px",
          }}
        >
          <Activity size={14} />
          VOL-TARGETING MODEL
          <InfoTooltip text={SECTION_TOOLTIPS["VOL-TARGETING MODEL"]} />
        </div>

        <div className="regime-cta-rows">
          <div className="regime-cta-row">
            <span>Implied Exposure</span>
            <span className={exposurePct != null && exposurePct < 50 ? "text-negative" : ""}>
              {fmt(exposurePct, 1)}%
            </span>
          </div>
          <div className="regime-cta-row">
            <span>Forced Reduction</span>
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
            <span>Est. CTA Selling</span>
            <span
              className={
                cta?.est_selling_bn && cta.est_selling_bn > 50 ? "text-negative" : ""
              }
            >
              ${fmt(cta?.est_selling_bn, 1)}B
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
            alignItems: "baseline",
            gap: "8px",
          }}
        >
          MENTHORQ CTA POSITIONING — {ctaData?.date ?? "---"}{" "}
          <InfoTooltip text={SECTION_TOOLTIPS["MENTHORQ CTA POSITIONING"]} />
          {fetchLabel && (
            <span style={{ fontWeight: 400, fontSize: "9px", color: "var(--text-muted)", letterSpacing: "0.06em" }}>
              · FETCHED {fetchLabel}
            </span>
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
          <div style={{ width: "100%", display: "flex", flexDirection: "column", gap: "8px", padding: "8px 0" }}>
            {order.map((key) => {
              const rows = ctaData.tables![key];
              if (!rows || rows.length === 0) return null;
              return <SortableCtaTable key={key} sectionKey={key} rows={rows} />;
            })}
          </div>
        )}
      </div>
    </div>
  );
}
