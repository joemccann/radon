"use client";

import { CircleDashed, Minus, TrendingDown, TrendingUp } from "lucide-react";
import { useMemo } from "react";
import { describeFlowSkew, type FlowSkew, type FlowSkewPath, type FlowSkewSession } from "@/lib/flowSkew";

type Props = {
  skew: FlowSkew | undefined;
  /** Mobile: one tile with the same reading, no section chrome. */
  compact?: boolean;
};

const PATH_ICON: Record<FlowSkewPath, typeof TrendingUp> = {
  rising: TrendingUp,
  falling: TrendingDown,
  flat: Minus,
  unknown: CircleDashed,
};

export default function FlowSkewPanel({ skew, compact = false }: Props) {
  const view = useMemo(() => describeFlowSkew(skew), [skew]);
  const path: FlowSkewPath = skew?.path ?? "unknown";
  const Icon = PATH_ICON[path];
  const sessions = skew?.sessions ?? [];

  const reading = (
    <div className="ticker-flow-skew-body">
      <div className="ticker-flow-skew-figure">
        <span className="ticker-flow-skew-value" data-testid="flow-skew-value">{view.valueLabel}</span>
        <span className="ticker-flow-skew-unit">{view.unitLabel}</span>
      </div>
      <div className={`ticker-flow-skew-path tone-${view.tone}`}>
        <Icon size={14} strokeWidth={2.25} aria-hidden="true" />
        <span data-testid="flow-skew-path">{view.pathLabel}</span>
      </div>
      <div className="ticker-flow-skew-spark">
        {view.available && sessions.length >= 2 && <SkewSparkline sessions={sessions} />}
      </div>
      <div className="ticker-flow-skew-change">{view.changeLabel ?? ""}</div>
      {compact && <div className="ticker-flow-skew-expiry">{view.expiryLabel ?? ""}</div>}
      <p className="ticker-flow-skew-note">{view.note}</p>
    </div>
  );

  if (compact) {
    return (
      <div
        className="ticker-flow-skew ticker-flow-skew-compact"
        data-testid="flow-skew-panel"
        data-path={path}
        data-tone={view.tone}
      >
        <div className="ticker-flow-skew-eyebrow">
          <span>Volatility Skew</span>
          <span className="ticker-flow-skew-eyebrow-meta">25Δ put minus call</span>
        </div>
        {reading}
      </div>
    );
  }

  return (
    <section
      className="section ticker-flow-skew"
      data-testid="flow-skew-panel"
      data-path={path}
      data-tone={view.tone}
    >
      <div className="section-header">
        <div className="section-title">Volatility Skew</div>
        <div className="report-meta" style={{ margin: 0 }}>
          25Δ put minus call{view.expiryLabel ? ` · ${view.expiryLabel}` : ""}
        </div>
      </div>
      <div className="section-body">{reading}</div>
    </section>
  );
}

const SPARK_W = 240;
const SPARK_H = 40;
const SPARK_PAD = 4;

function SkewSparkline({ sessions }: { sessions: FlowSkewSession[] }) {
  const points = useMemo(() => {
    const values = sessions.map((session) => session.value);
    const min = Math.min(...values);
    const max = Math.max(...values);
    const span = max - min || 1;
    const stepX = (SPARK_W - SPARK_PAD * 2) / Math.max(1, sessions.length - 1);
    return values.map((value, index) => ({
      x: SPARK_PAD + index * stepX,
      y: SPARK_PAD + (1 - (value - min) / span) * (SPARK_H - SPARK_PAD * 2),
    }));
  }, [sessions]);
  const polyline = points.map((point) => `${point.x.toFixed(1)},${point.y.toFixed(1)}`).join(" ");
  const first = sessions[0]?.date ?? "";
  const last = sessions[sessions.length - 1]?.date ?? "";

  return (
    <svg
      className="ticker-flow-skew-sparkline"
      data-testid="flow-skew-sparkline"
      viewBox={`0 0 ${SPARK_W} ${SPARK_H}`}
      preserveAspectRatio="xMinYMid meet"
      role="img"
      aria-label={`Skew over ${sessions.length} sessions, ${first} to ${last}`}
    >
      <polyline points={polyline} fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinejoin="round" strokeLinecap="round" />
      {points.map((point, index) => (
        <circle
          key={sessions[index].date}
          cx={point.x}
          cy={point.y}
          r={index === points.length - 1 ? 2.75 : 1.5}
          fill={index === points.length - 1 ? "currentColor" : "var(--bg-panel)"}
          stroke="currentColor"
          strokeWidth={1}
        />
      ))}
    </svg>
  );
}
