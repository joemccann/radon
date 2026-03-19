"use client";

import { Activity, Share2 } from "lucide-react";
import { useState } from "react";
import { useRegime } from "@/lib/useRegime";
import { useMenthorqCta } from "@/lib/useMenthorqCta";
import { SECTION_TOOLTIPS } from "@/lib/sectionTooltips";
import InfoTooltip from "./InfoTooltip";
import SortableCtaTable, { type CtaSectionCallout } from "./SortableCtaTable";
import CtaBriefing from "./CtaBriefing";

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

  /* ─── Share state ──────────────────────────────────── */
  const [sharing, setSharing] = useState(false);
  const [shareError, setShareError] = useState<string | null>(null);

  async function handleShare() {
    setSharing(true);
    setShareError(null);
    try {
      // Step 1: trigger generation
      const res = await fetch("/api/menthorq/cta/share", { method: "POST" });
      const data = await res.json() as { preview_path?: string; error?: string };
      if (!res.ok) {
        setShareError(data?.error ?? "Share generation failed");
        return;
      }
      // Step 2: fetch the generated HTML and open as blob URL (avoids file:// CORS)
      const previewPath = data?.preview_path;
      if (previewPath) {
        const htmlRes = await fetch(`/api/menthorq/cta/share/content?path=${encodeURIComponent(previewPath)}`);
        if (htmlRes.ok) {
          const html = await htmlRes.text();
          const blob = new Blob([html], { type: "text/html" });
          const url = URL.createObjectURL(blob);
          window.open(url, "_blank");
          // Clean up after a delay
          setTimeout(() => URL.revokeObjectURL(url), 60_000);
        } else {
          // Fallback: use the OS open command via a server-side trigger
          setShareError("Preview generated. Open: " + previewPath);
        }
      }
    } catch (e) {
      setShareError(e instanceof Error ? e.message : "Unknown error");
    } finally {
      setSharing(false);
    }
  }

  /* ─── Per-section callouts ─────────────────────────── */
  function buildCallout(key: typeof order[number], tables: NonNullable<typeof ctaData>["tables"]): CtaSectionCallout | undefined {
    if (!tables) return undefined;
    const rows = tables[key];
    if (!rows || rows.length === 0) return undefined;

    switch (key) {
      case "main": {
        const spx = rows.find(r => r.underlying.toLowerCase().includes("s&p") || r.underlying.toLowerCase().includes("e-mini"));
        const bonds = rows.filter(r => r.underlying.toLowerCase().includes("t-note") || r.underlying.toLowerCase().includes("treasury"));
        if (spx && spx.percentile_3m <= 10) {
          const flipped = spx.position_1m_ago > 0 && spx.position_today < 0;
          const bondShortCount = bonds.filter(b => b.percentile_3m <= 10).length;
          return {
            kind: "short",
            headline: `MAX SHORT · ${spx.percentile_3m === 0 ? "0th" : spx.percentile_3m + "th"} pctile (3M), z ${spx.z_score_3m.toFixed(2)}.`,
            body: [
              flipped ? `Flipped from ${spx.position_1m_ago.toFixed(2)} long one month ago.` : null,
              bondShortCount >= 2 ? `${bondShortCount} bond contracts at 0th pctile — full duration short.` : null,
              "Violent short-covering likely on any bullish catalyst.",
            ].filter(Boolean).join(" "),
          };
        }
        if (spx && spx.percentile_3m >= 85) {
          return { kind: "long", headline: `HEAVY LONG · ${spx.percentile_3m}th pctile.`, body: "CTA equity exposure elevated. Watch for mean reversion on disappointing macro." };
        }
        return undefined;
      }
      case "index": {
        const extreme = rows.filter(r => r.percentile_3m <= 5 && r.position_today < 0);
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
        const crowdedLongs = rows.filter(r => r.percentile_3m >= 85 && r.position_today > 0).sort((a, b) => b.percentile_3m - a.percentile_3m);
        if (crowdedLongs.length >= 2) {
          const labels = crowdedLongs.slice(0, 3).map(r => `${r.underlying.split(" ")[0]} ${r.percentile_3m}th`).join(", ");
          return {
            kind: "long",
            headline: `CROWDED LONGS · ${labels}.`,
            body: "Mean reversion risk elevated on energy and soft commodities. Stagflation trade at historically extreme levels.",
          };
        }
        const gold = rows.find(r => r.underlying.toLowerCase().includes("gold"));
        if (gold && gold.percentile_3m <= 10 && gold.position_today > 0 && gold.position_yesterday > gold.position_today) {
          return { kind: "neutral", headline: "GOLD REDUCING.", body: `Position softening (${gold.position_yesterday.toFixed(2)} to ${gold.position_today.toFixed(2)}) despite elevated spot price. CTA reduction signal.` };
        }
        return undefined;
      }
      case "currency": {
        const dxy = rows.find(r => r.underlying.toLowerCase().includes("dollar"));
        const extreme = rows.filter(r => r.percentile_3m <= 10 && r.position_today < 0);
        if (dxy && dxy.percentile_3m >= 85 && extreme.length >= 2) {
          return {
            kind: "long",
            headline: `LONG USD · ${dxy.percentile_3m}th pctile.`,
            body: `${extreme.length} currency pairs short at extreme levels. Dollar strength trade crowded — watch for reversal if risk appetite returns.`,
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
            <button
              onClick={handleShare}
              disabled={sharing}
              title="Share CTA report to X"
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: "5px",
                padding: "4px 10px",
                background: sharing ? "var(--bg-hover)" : "transparent",
                border: "1px solid var(--border-dim)",
                borderRadius: "3px",
                fontFamily: "var(--font-mono, monospace)",
                fontSize: "9px",
                fontWeight: 700,
                letterSpacing: "0.08em",
                textTransform: "uppercase",
                color: sharing ? "var(--text-muted)" : "var(--text-primary)",
                cursor: sharing ? "not-allowed" : "pointer",
                transition: "all 150ms",
                flexShrink: 0,
              }}
              onMouseEnter={e => { if (!sharing) (e.currentTarget as HTMLButtonElement).style.borderColor = "var(--signal-core)"; (e.currentTarget as HTMLButtonElement).style.color = "var(--signal-core)"; }}
              onMouseLeave={e => { (e.currentTarget as HTMLButtonElement).style.borderColor = "var(--border-dim)"; (e.currentTarget as HTMLButtonElement).style.color = sharing ? "var(--text-muted)" : "var(--text-primary)"; }}
            >
              <Share2 size={11} />
              {sharing ? "Generating…" : "Share to X"}
            </button>
          )}
        </div>
        {shareError && (
          <div style={{
            margin: "8px 12px",
            padding: "7px 10px",
            border: "1px solid var(--negative)",
            borderRadius: "3px",
            background: "rgba(232,93,108,0.06)",
            fontFamily: "var(--font-mono, monospace)",
            fontSize: "10px",
            color: "var(--negative)",
          }}>
            {shareError}
          </div>
        )}

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
              estSellingBn={cta?.est_selling_bn ?? null}
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
  );
}
