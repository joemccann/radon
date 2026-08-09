"use client";

import type { ReactNode } from "react";

/**
 * InstrumentPanel — the production-grade primitive every hero / signal-summary
 * card extends. Encodes the brand-identity.md § 4 container grammar:
 *
 *   ┌─────────────────────────────────────────────────┐
 *   │  SIGNAL CANDIDATE / 01            ◆ DISLOCATED │ ← eyebrow + badge
 *   │  Vol-Credit Gap                                │ ← title
 *   │                                                │
 *   │  +2.73σ                                        │ ← metric
 *   │  20-SESSION BASELINE DEVIATION                 │ ← metricLabel
 *   │                                                │
 *   │  vix.vvix.ratio                           5.4  │ ← meta rail
 *   │  engine                                 Eigen  │
 *   │  basis                           20d residual  │
 *   └─────────────────────────────────────────────────┘
 *
 * The four dashboard hero cards (CRI Composite / Vol Dislocation / Markov
 * State / Portfolio Convexity) are all thin wrappers around this primitive
 * with slot-specific data wiring.
 */

export type PanelTone =
  | "core"
  | "warn"
  | "fault"
  | "dislocation"
  | "extreme"
  | "neutral";

export type InstrumentPanelMetaRow = {
  k: string;
  v: ReactNode;
};

export type InstrumentPanelProps = {
  /** Mono uppercase line above the title — module ID + cell. */
  eyebrow: string;
  /** Sans semibold, panel name. */
  title: string;
  /** Status pill rendered top-right. Omit for headerless panels. */
  badge?: { text: string; tone: PanelTone };
  /** The big number / state value. */
  metric: ReactNode;
  /** Mono uppercase label beneath the metric. */
  metricLabel: string;
  /** Meta-rail rows along the bottom. Convention: lowercased.dotted keys. */
  meta: InstrumentPanelMetaRow[];
  /** Force the metric to a muted "awaiting feed" rendering. */
  awaiting?: boolean;
};

export function InstrumentPanel({
  eyebrow,
  title,
  badge,
  metric,
  metricLabel,
  meta,
  awaiting = false,
}: InstrumentPanelProps) {
  return (
    <section className="instrument-panel">
      <header className="instrument-panel__header">
        <div className="instrument-panel__heading">
          <p className="panel-eyebrow">{eyebrow}</p>
          <h3 className="panel-title">{title}</h3>
        </div>
        {badge ? (
          <span className={`instrument-badge instrument-badge-${badge.tone}`}>
            {badge.text}
          </span>
        ) : null}
      </header>

      <div
        className={`instrument-panel__metric ${
          awaiting ? "instrument-panel__metric--awaiting" : ""
        }`}
      >
        <div className="instrument-panel__metric-value">
          {awaiting ? "—" : metric}
        </div>
        <div className="instrument-panel__metric-label">{metricLabel}</div>
      </div>

      <dl className="instrument-panel__meta">
        {meta.map((row) => (
          <div key={row.k} className="instrument-panel__meta-row">
            <dt>{row.k}</dt>
            <dd>{row.v}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}
