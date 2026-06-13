"use client";

/**
 * LocateFeeChip — short-locate status indicator rendered inside OrderRiskGate.
 *
 * Shown only when:
 *   - The order action is SELL or SHORT
 *   - The account holds NO position in the underlying (net zero or no leg)
 *
 * Visual states:
 *   - Red   "NO LOCATE"          — missing data or not shortable
 *   - Amber "HTB · {fee}%"       — hard to borrow, locate-only with fee
 *   - Green "EASY · {shares}"    — easy to borrow, shares available
 *
 * Includes as_of and source in a tooltip / secondary line.
 */

import type { ShortAvailabilityData, ShortAvailabilityStatus } from "../hooks/useShortAvailability";

export interface LocateFeeChipProps {
  status: ShortAvailabilityStatus;
  data: ShortAvailabilityData;
}

function formatAsOf(isoString: string): string {
  try {
    return new Date(isoString).toLocaleTimeString("en-US", {
      hour: "2-digit",
      minute: "2-digit",
      timeZoneName: "short",
    });
  } catch {
    return isoString;
  }
}

function formatShares(shares: number | null): string {
  if (shares == null) return "";
  if (shares >= 1_000_000) return `${(shares / 1_000_000).toFixed(1)}M`;
  if (shares >= 1_000) return `${(shares / 1_000).toFixed(0)}K`;
  return shares.toLocaleString("en-US");
}

function chipLabel(
  status: ShortAvailabilityStatus,
  data: ShortAvailabilityData,
): string {
  if (status === "no-locate") return "NO LOCATE";
  if (status === "htb") {
    const fee = data.fee_rate != null ? `${data.fee_rate.toFixed(2)}%` : "";
    return fee ? `HTB · ${fee}` : "HTB";
  }
  const shares = data.shortable_shares != null ? ` · ${formatShares(data.shortable_shares)}` : "";
  return `EASY${shares}`;
}

function chipStyle(status: ShortAvailabilityStatus): React.CSSProperties {
  const base: React.CSSProperties = {
    display: "inline-flex",
    alignItems: "center",
    gap: "4px",
    fontSize: "11px",
    fontWeight: 600,
    letterSpacing: "0.05em",
    padding: "2px 8px",
    borderRadius: "4px",
    border: "1px solid",
    lineHeight: 1.4,
    whiteSpace: "nowrap",
  };

  if (status === "no-locate") {
    return {
      ...base,
      color: "var(--negative)",
      borderColor: "color-mix(in srgb, var(--negative) 40%, transparent)",
      background: "color-mix(in srgb, var(--negative) 8%, transparent)",
    };
  }
  if (status === "htb") {
    return {
      ...base,
      color: "var(--warning)",
      borderColor: "color-mix(in srgb, var(--warning) 40%, transparent)",
      background: "color-mix(in srgb, var(--warning) 8%, transparent)",
    };
  }
  // easy
  return {
    ...base,
    color: "var(--positive)",
    borderColor: "color-mix(in srgb, var(--positive) 40%, transparent)",
    background: "color-mix(in srgb, var(--positive) 8%, transparent)",
  };
}

/** React is needed for JSX; kept as namespace import to satisfy jsdom env. */
import React from "react";

export function LocateFeeChip({ status, data }: LocateFeeChipProps) {
  const label = chipLabel(status, data);
  const asOf = formatAsOf(data.as_of);
  const source = data.source !== "none" ? data.source.toUpperCase() : null;

  return (
    <div
      className="locate-fee-chip-wrapper"
      style={{ display: "flex", flexDirection: "column", gap: "2px" }}
      data-testid="locate-fee-chip"
      data-status={status}
    >
      <span style={chipStyle(status)} aria-label={`Short availability: ${label}`}>
        {label}
      </span>
      <span
        style={{
          fontSize: "10px",
          color: "var(--text-secondary)",
          letterSpacing: "0.02em",
        }}
      >
        {[source, asOf ? `as of ${asOf}` : null].filter(Boolean).join(" · ")}
      </span>
    </div>
  );
}
