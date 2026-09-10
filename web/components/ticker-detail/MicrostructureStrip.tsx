"use client";

import { useMemo } from "react";
import type { DepthBook } from "@/lib/pricesProtocol";
import {
  deriveMicrostructure,
  DEFAULT_MICRO_TOP_N,
} from "@/lib/book/microstructure";

/**
 * Compact imbalance / microprice strip for the L2 Book tab (F10).
 *
 * Reads the focused DepthBook, derives the resting-pressure imbalance and the
 * size-weighted microprice (pure derivation in `lib/book/microstructure.ts`),
 * and renders a one-line readout: a centered bar showing where the imbalance
 * sits on the [-1, +1] axis, the signed imbalance value, and the microprice
 * with its premium over the mid. Renders nothing when there is no entitled
 * depth book (off-hours / unentitled), matching the rest of the Book tab's
 * degrade-to-L1 behaviour.
 *
 * Trade-direction toxicity (VPIN) is intentionally absent — the tape feed is
 * still Phase 3.
 */

const BAR_HALF_WIDTH_PCT = 50;

function formatSigned(value: number, digits: number): string {
  const fixed = value.toFixed(digits);
  return value > 0 ? `+${fixed}` : fixed;
}

function pressureLabel(imbalance: number): string {
  if (imbalance > 0.05) return "BID";
  if (imbalance < -0.05) return "ASK";
  return "BALANCED";
}

export function MicrostructureStrip({
  depth,
  topN = DEFAULT_MICRO_TOP_N,
}: {
  depth: DepthBook | null | undefined;
  topN?: number;
}) {
  const micro = useMemo(() => deriveMicrostructure(depth, topN), [depth, topN]);

  if (!micro || micro.imbalance == null) return null;

  const imbalance = micro.imbalance;
  const leansBid = imbalance >= 0;
  const directionToken = leansBid ? "var(--positive)" : "var(--negative)";
  // Bar grows from the center toward the leaning side; width scales with |imb|.
  const fillPct = Math.min(Math.abs(imbalance), 1) * BAR_HALF_WIDTH_PCT;

  return (
    <div
      className="microstructure-strip"
      style={{
        display: "flex",
        alignItems: "center",
        // Wrap on narrow viewports: the nowrap "(±x.xx vs mid)" tail must
        // drop to a second row, never push the document wider than the phone.
        flexWrap: "wrap",
        gap: "6px 16px",
        minWidth: 0,
        padding: "8px 12px",
        marginTop: "12px",
        border: "1px solid var(--line-grid)",
        borderRadius: "4px",
        background: "color-mix(in srgb, var(--bg-panel-raised) 60%, transparent)",
        fontFamily: "var(--font-mono)",
        fontSize: "var(--text-meta)",
      }}
    >
      <Metric label="IMBALANCE">
        <span style={{ color: directionToken }}>
          {formatSigned(imbalance, 2)}
        </span>{" "}
        <span style={{ color: "var(--text-muted)" }}>
          {pressureLabel(imbalance)}
        </span>
      </Metric>

      {/* Center-anchored pressure bar: fills left for ASK, right for BID. */}
      <div
        aria-hidden
        style={{
          position: "relative",
          flex: 1,
          height: "6px",
          minWidth: "60px",
          borderRadius: "3px",
          background: "color-mix(in srgb, var(--line-grid) 60%, transparent)",
        }}
      >
        <div
          style={{
            position: "absolute",
            top: 0,
            bottom: 0,
            left: leansBid ? "50%" : `${BAR_HALF_WIDTH_PCT - fillPct}%`,
            width: `${fillPct}%`,
            borderRadius: "3px",
            background: directionToken,
          }}
        />
        <div
          style={{
            position: "absolute",
            top: "-2px",
            bottom: "-2px",
            left: "50%",
            width: "1px",
            background: "var(--text-muted)",
          }}
        />
      </div>

      <Metric label="MICROPRICE">
        {micro.microprice != null ? (
          <>
            <span style={{ color: "var(--signal-core-text)" }}>
              {micro.microprice.toFixed(2)}
            </span>
            {micro.microPremium != null && (
              <span style={{ color: "var(--text-muted)" }}>
                {" "}
                ({formatSigned(micro.microPremium, 2)} vs mid)
              </span>
            )}
          </>
        ) : (
          <span style={{ color: "var(--text-muted)" }}>---</span>
        )}
      </Metric>
    </div>
  );
}

function Metric({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: "6px",
        whiteSpace: "nowrap",
      }}
    >
      <span
        style={{
          color: "var(--text-secondary)",
          letterSpacing: "0.08em",
        }}
      >
        {label}
      </span>
      <span>{children}</span>
    </div>
  );
}
