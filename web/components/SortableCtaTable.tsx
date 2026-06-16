"use client";

import { useState } from "react";
import type { CtaRow } from "@/lib/useMenthorqCta";
import { SECTION_TOOLTIPS } from "@/lib/sectionTooltips";
import InfoTooltip from "./InfoTooltip";
import { normalizeCtaPercentile } from "@/lib/ctaPercentiles";
import { useViewport } from "@/lib/useViewport";
import { useSort } from "@/lib/useSort";
import SortTh from "./SortTh";
import MetricCell from "./mobile/MetricCell";
import { ChevronRight, ChevronDown } from "lucide-react";

/* ─── Props ──────────────────────────────────────────── */

export type CtaSectionCallout = {
  headline: string;
  body: string;
  kind: "short" | "long" | "neutral";
};

type SortableCtaTableProps = {
  sectionKey: string;
  rows: CtaRow[];
  callout?: CtaSectionCallout;
};

/* ─── Helpers ────────────────────────────────────────── */

function fmt(v: number | null | undefined, decimals = 2): string {
  if (v == null || !Number.isFinite(v)) return "---";
  return v.toFixed(decimals);
}

function fmtPctile(v: number | null | undefined): string {
  const normalized = normalizeCtaPercentile(v);
  if (normalized == null) return "---";
  return String(Math.round(normalized));
}

function posColor(v: number): string {
  if (v > 0) return "var(--positive)";
  if (v < 0) return "var(--negative)";
  return "var(--text-primary)";
}

function pctileBg(v: number): string {
  const normalized = normalizeCtaPercentile(v) ?? v;
  if (normalized <= 10) return "color-mix(in srgb, var(--fault) 25%, transparent)";
  if (normalized <= 25) return "color-mix(in srgb, var(--fault) 12%, transparent)";
  if (normalized <= 40) return "color-mix(in srgb, var(--warning) 12%, transparent)";
  if (normalized >= 75) return "color-mix(in srgb, var(--signal-core) 25%, transparent)";
  if (normalized >= 60) return "color-mix(in srgb, var(--signal-core) 12%, transparent)";
  return "transparent";
}

function zColor(z: number): string {
  if (z > 0) return "var(--positive)";
  if (z < 0) return "var(--negative)";
  return "var(--text-primary)";
}

function zOpacity(z: number): number {
  const abs = Math.abs(z);
  if (abs >= 2) return 1;
  if (abs >= 1) return 0.85;
  if (abs >= 0.5) return 0.7;
  return 0.55;
}

/* ─── Constants ──────────────────────────────────────── */

const SECTION_LABELS: Record<string, string> = {
  main: "MAIN INDICES",
  index: "INDEX FUTURES",
  commodity: "COMMODITIES",
  currency: "CURRENCIES",
};

type CtaSortKey =
  | "position_today"
  | "position_yesterday"
  | "position_1m_ago"
  | "percentile_1m"
  | "percentile_3m"
  | "percentile_1y"
  | "z_score_3m";

function ctaSortValue(row: CtaRow, key: CtaSortKey): number {
  return row[key] as number;
}

/* ─── Flag helpers ───────────────────────────────────── */

function flagForRow(r: CtaRow): { kind: "short" | "long"; tooltip: string } | null {
  const p = normalizeCtaPercentile(r.percentile_3m) ?? r.percentile_3m;
  const z = r.z_score_3m;
  const isExtreme = p <= 10 || p >= 90 || Math.abs(z) >= 1.5;
  if (!isExtreme) return null;

  const isShort = r.position_today < 0 && (p <= 10 || z <= -1.5);
  const isLong  = r.position_today > 0 && (p >= 90 || z >= 1.5);

  if (isShort) {
    const flipped = r.position_1m_ago > 0;
    return {
      kind: "short",
      tooltip: [
        `${Math.round(p)}th pctile (3M), z ${fmt(z)}.`,
        flipped ? `Flipped from ${fmt(r.position_1m_ago)} long 1M ago.` : null,
        Math.abs(z) >= 2.0
          ? "Extreme short. Violent covering risk on any bullish catalyst."
          : "Heavy short positioning.",
      ].filter(Boolean).join(" "),
    };
  }
  if (isLong) {
    return {
      kind: "long",
      tooltip: [
        `${Math.round(p)}th pctile (3M), z ${fmt(z)}.`,
        "Crowded long. Mean reversion risk elevated.",
      ].join(" "),
    };
  }
  return null;
}

/* ─── Mobile sort key -> label ───────────────────────── */

type MobileSortOption = { key: CtaSortKey; label: string };

const MOBILE_SORT_OPTIONS: MobileSortOption[] = [
  { key: "position_today", label: "Today" },
  { key: "z_score_3m", label: "Z-score" },
  { key: "percentile_3m", label: "%ile" },
];

function flagDotColor(kind: "short" | "long"): string {
  return kind === "short" ? "var(--negative)" : "var(--positive)";
}

function flagLabel(kind: "short" | "long"): string {
  return kind === "short" ? "HEAVY SHORT" : "CROWDED LONG";
}

/* ─── Mobile section ─────────────────────────────────── */

const MOBILE_TOP_N = 5;

function MobileCtaSection({ sectionKey, rows, callout }: SortableCtaTableProps) {
  const [activeSortKey, setActiveSortKey] = useState<CtaSortKey>("position_today");
  const [expanded, setExpanded] = useState(false);
  const { sorted } = useSort<CtaRow, CtaSortKey>(rows, ctaSortValue, activeSortKey);

  const visibleRows = expanded ? sorted : sorted.slice(0, MOBILE_TOP_N);
  const hasMore = sorted.length > MOBILE_TOP_N;
  const sectionLabel = SECTION_LABELS[sectionKey] ?? sectionKey.toUpperCase();

  return (
    <div style={{ width: "100%" }}>
      {/* Section header row */}
      <div className="m-cta-section-header tap-target" role="heading" aria-level={3}>
        <span className="m-cta-section-header__label">
          {sectionLabel}
          {SECTION_TOOLTIPS[sectionLabel] && (
            <InfoTooltip text={SECTION_TOOLTIPS[sectionLabel]} />
          )}
        </span>
        <span className="m-cta-section-header__count">{rows.length}</span>
        <ChevronRight size={14} color="var(--text-muted)" aria-hidden />
      </div>

      {/* Sort bar */}
      <div className="m-sortbar" role="group" aria-label="Sort CTA rows">
        {MOBILE_SORT_OPTIONS.map((opt) => (
          <button
            key={opt.key}
            type="button"
            className={`m-chip${activeSortKey === opt.key ? " m-chip--active" : ""}`}
            onClick={() => setActiveSortKey(opt.key)}
          >
            {opt.label}
          </button>
        ))}
      </div>

      {callout && (
        <div
          className="cta-section-callout"
          style={{
            borderLeftColor: callout.kind === "short"
              ? "var(--negative)"
              : callout.kind === "long"
                ? "var(--signal-core)"
                : "var(--border-dim)",
          }}
        >
          <span
            className="cta-section-callout-headline"
            style={{
              color: callout.kind === "short"
                ? "var(--negative)"
                : callout.kind === "long"
                  ? "var(--signal-core)"
                  : "var(--text-muted)",
            }}
          >
            {callout.headline}
          </span>
          {" "}
          <span className="cta-section-callout-body">{callout.body}</span>
        </div>
      )}

      <div className="cta-mobile-list" data-testid="cta-mobile-list">
        {visibleRows.map((r) => {
          const flag = flagForRow(r);
          const todayTone = r.position_today > 0 ? "pos" as const : r.position_today < 0 ? "neg" as const : "mut" as const;
          const zTone = r.z_score_3m > 1 ? "pos" as const : r.z_score_3m < -1 ? "neg" as const : "mut" as const;
          return (
            <article key={r.underlying} className="m-card-press cta-mobile-card">
              <div className="cta-mobile-card__head">
                <span className="cta-mobile-card__ticker">{r.underlying}</span>
                {flag ? (
                  <span
                    className="m-cta-flag-inline"
                    title={flag.tooltip}
                    aria-label={flag.tooltip}
                  >
                    <span
                      className="m-cta-flag-dot"
                      style={{ background: flagDotColor(flag.kind) }}
                      aria-hidden
                    />
                    <span
                      className="m-cta-flag-label"
                      style={{ color: flagDotColor(flag.kind) }}
                    >
                      {flagLabel(flag.kind)}
                    </span>
                  </span>
                ) : null}
              </div>
              <div className="cta-mobile-card__grid">
                <MetricCell label="Today" value={fmt(r.position_today)} tone={todayTone} />
                <MetricCell label="3M %ile" value={fmtPctile(r.percentile_3m)} />
                <MetricCell label="3M Z" value={fmt(r.z_score_3m)} tone={zTone} />
                <MetricCell label="1M ago" value={fmt(r.position_1m_ago)} tone={r.position_1m_ago > 0 ? "pos" : r.position_1m_ago < 0 ? "neg" : "mut"} />
              </div>
            </article>
          );
        })}
      </div>

      {hasMore && (
        <button
          type="button"
          className="m-cta-expander tap-target"
          onClick={() => setExpanded((x) => !x)}
        >
          {expanded ? (
            <>
              <ChevronDown size={14} aria-hidden />
              Show fewer
            </>
          ) : (
            <>
              <ChevronRight size={14} aria-hidden />
              Show all {sorted.length}
            </>
          )}
        </button>
      )}
    </div>
  );
}

/* ─── Component ──────────────────────────────────────── */

export default function SortableCtaTable({ sectionKey, rows, callout }: SortableCtaTableProps) {
  const { sorted, sort, toggle } = useSort<CtaRow, CtaSortKey>(rows, ctaSortValue);
  const { isMobile, hasMounted } = useViewport();

  if (hasMounted && isMobile) {
    return (
      <div data-testid="sortable-cta-table" style={{ width: "100%" }}>
        <MobileCtaSection sectionKey={sectionKey} rows={rows} callout={callout} />
      </div>
    );
  }

  return (
    <div data-testid="sortable-cta-table" style={{ width: "100%" }}>
      <div
        style={{
          fontFamily: "var(--font-mono, monospace)",
          fontSize: "10px",
          fontWeight: 700,
          letterSpacing: "0.10em",
          color: "var(--text-muted)",
          padding: "8px 12px 4px",
          textTransform: "uppercase",
        }}
      >
        {SECTION_LABELS[sectionKey] ?? sectionKey.toUpperCase()}
        {SECTION_TOOLTIPS[SECTION_LABELS[sectionKey]] && (
          <InfoTooltip text={SECTION_TOOLTIPS[SECTION_LABELS[sectionKey]]} />
        )}
        <span
          style={{
            marginLeft: "8px",
            fontSize: "9px",
            fontWeight: 400,
            background: "color-mix(in srgb, var(--text-primary) 6%, transparent)",
            padding: "1px 5px",
            letterSpacing: "0.04em",
          }}
        >
          {rows.length}
        </span>
      </div>
      {callout && (
        <div
          className="cta-section-callout"
          style={{
            borderLeftColor: callout.kind === "short"
              ? "var(--negative)"
              : callout.kind === "long"
                ? "var(--signal-core)"
                : "var(--border-dim)",
          }}
        >
          <span
            className="cta-section-callout-headline"
            style={{
              color: callout.kind === "short"
                ? "var(--negative)"
                : callout.kind === "long"
                  ? "var(--signal-core)"
                  : "var(--text-muted)",
            }}
          >
            {callout.headline}
          </span>
          {" "}
          <span className="cta-section-callout-body">{callout.body}</span>
        </div>
      )}
      <div className="cta-table-wrap" style={{ width: "100%" }}>
        <table className="cta-table" style={{ width: "100%" }}>
          <thead>
            <tr>
              <th className="cta-th-underlying">UNDERLYING</th>
              <SortTh<CtaSortKey> label="TODAY" sortKey="position_today" activeKey={sort.key} direction={sort.direction} onToggle={toggle} className="cta-th-num" />
              <SortTh<CtaSortKey> label="YDAY" sortKey="position_yesterday" activeKey={sort.key} direction={sort.direction} onToggle={toggle} className="cta-th-num" />
              <SortTh<CtaSortKey> label="1M AGO" sortKey="position_1m_ago" activeKey={sort.key} direction={sort.direction} onToggle={toggle} className="cta-th-num" />
              <SortTh<CtaSortKey> label="1M %ILE" sortKey="percentile_1m" activeKey={sort.key} direction={sort.direction} onToggle={toggle} className="cta-th-num" />
              <SortTh<CtaSortKey> label="3M %ILE" sortKey="percentile_3m" activeKey={sort.key} direction={sort.direction} onToggle={toggle} className="cta-th-num" />
              <SortTh<CtaSortKey> label="1Y %ILE" sortKey="percentile_1y" activeKey={sort.key} direction={sort.direction} onToggle={toggle} className="cta-th-num" />
              <SortTh<CtaSortKey> label="3M Z" sortKey="z_score_3m" activeKey={sort.key} direction={sort.direction} onToggle={toggle} className="cta-th-num" />
              <th style={{ width: "24px" }} aria-label="signal flag" />
            </tr>
          </thead>
          <tbody>
            {sorted.map((r) => {
              const flag = flagForRow(r);
              return (
                <tr key={r.underlying}>
                  <td className="cta-td-underlying">{r.underlying}</td>
                  <td className="cta-td-num" style={{ color: posColor(r.position_today) }}>
                    {fmt(r.position_today)}
                  </td>
                  <td className="cta-td-num" style={{ color: posColor(r.position_yesterday) }}>
                    {fmt(r.position_yesterday)}
                  </td>
                  <td className="cta-td-num" style={{ color: posColor(r.position_1m_ago) }}>
                    {fmt(r.position_1m_ago)}
                  </td>
                  <td className="cta-td-num" style={{ background: pctileBg(r.percentile_1m) }}>
                    {fmtPctile(r.percentile_1m)}
                  </td>
                  <td className="cta-td-num" style={{ background: pctileBg(r.percentile_3m) }}>
                    {fmtPctile(r.percentile_3m)}
                  </td>
                  <td className="cta-td-num" style={{ background: pctileBg(r.percentile_1y) }}>
                    {fmtPctile(r.percentile_1y)}
                  </td>
                  <td
                    className="cta-td-num"
                    style={{ color: zColor(r.z_score_3m), opacity: zOpacity(r.z_score_3m) }}
                  >
                    {fmt(r.z_score_3m)}
                  </td>
                  <td className="cta-td-flag">
                    {flag && (
                      <span
                        className={`cta-flag cta-flag-${flag.kind}`}
                        title={flag.tooltip}
                        aria-label={flag.tooltip}
                      >
                        {flag.kind === "short" ? "!" : "^"}
                      </span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
