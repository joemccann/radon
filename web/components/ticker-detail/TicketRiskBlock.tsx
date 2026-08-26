"use client";

import { useMemo } from "react";
import { payoffCurve, type PayoffLeg } from "@/lib/order/payoff";

/**
 * Risk panel for the docked ticket rail.
 *
 * Rendered ABOVE the CTA on purpose: the operator reads max loss before the
 * transmit button, not after it.
 *
 * Every cell shows a real figure or "---". Nothing is inferred to fill a gap.
 * P(PROFIT) in particular needs a volatility model the order pipeline does not
 * currently produce, so it reads "---" rather than a plausible number someone
 * might size a position against.
 */

type TicketRiskBlockProps = {
  /** Per-combo legs, used only for the exact expiry payoff. */
  legs: PayoffLeg[];
  /** Signed per-share net premium: positive debit, negative credit. */
  netPremium: number;
  spot: number;
  maxGain: number | null;
  maxLoss: number | null;
  maxLossUnbounded: boolean;
  marginRequirement: number | null;
  fundsAfter: number | null;
  total: number | null;
  totalLabel?: string;
  isCredit: boolean;
};

const DASH = "---";

function usd(value: number | null, fractionDigits = 2): string {
  if (value == null || !Number.isFinite(value)) return DASH;
  return `$${Math.abs(value).toLocaleString("en-US", {
    minimumFractionDigits: fractionDigits,
    maximumFractionDigits: fractionDigits,
  })}`;
}

function Cell({ label, value, tone }: { label: string; value: string; tone?: "gain" | "loss" | "warn" }) {
  return (
    <div className="ticket-risk-cell">
      <div className="ticket-risk-cell-label">{label}</div>
      <div className={`ticket-risk-cell-value${tone ? ` ticket-risk-cell-value--${tone}` : ""}`}>{value}</div>
    </div>
  );
}

const CURVE_W = 336;
const CURVE_H = 72;

export default function TicketRiskBlock({
  legs,
  netPremium,
  spot,
  maxGain,
  maxLoss,
  maxLossUnbounded,
  marginRequirement,
  fundsAfter,
  total,
  totalLabel = "TOTAL",
  isCredit,
}: TicketRiskBlockProps) {
  const curve = useMemo(() => payoffCurve(legs, netPremium, { spot }), [legs, netPremium, spot]);

  const breakevenLabel =
    curve.breakevens.length === 0
      ? DASH
      : curve.breakevens.map((b) => b.toFixed(2)).join(" / ");

  const geometry = useMemo(() => {
    if (curve.points.length === 0) return null;
    const xs = curve.points.map((p) => p.underlying);
    const loX = Math.min(...xs);
    const hiX = Math.max(...xs);
    const spanX = hiX - loX || 1;
    // Pad the vertical range so a flat wing does not sit on the frame edge.
    const spanY = Math.max(curve.max - curve.min, 1e-6) * 1.15;
    const midY = (curve.max + curve.min) / 2;
    const loY = midY - spanY / 2;
    const toX = (u: number) => ((u - loX) / spanX) * CURVE_W;
    const toY = (p: number) => CURVE_H - ((p - loY) / spanY) * CURVE_H;
    return {
      toX,
      toY,
      zeroY: toY(0),
      polyline: curve.points.map((p) => `${toX(p.underlying).toFixed(1)},${toY(p.pnl).toFixed(1)}`).join(" "),
    };
  }, [curve]);

  return (
    <div className="ticket-risk" data-testid="ticket-risk">
      <div className="ticket-risk-head">
        <span>RISK · PER 1× COMBO</span>
      </div>

      <div className="ticket-risk-grid">
        <Cell label="MAX GAIN" value={usd(maxGain)} tone={maxGain != null ? "gain" : undefined} />
        <Cell
          label="MAX LOSS"
          value={maxLossUnbounded ? "UNBOUNDED" : usd(maxLoss)}
          tone={maxLossUnbounded || maxLoss != null ? "loss" : undefined}
        />
        <Cell label="BREAKEVENS" value={breakevenLabel} />
        <Cell label="P(PROFIT)" value={DASH} />
        <Cell
          label="MARGIN REQ"
          value={usd(marginRequirement, 0)}
          tone={marginRequirement != null ? "warn" : undefined}
        />
        <Cell label="FUNDS AFTER" value={usd(fundsAfter, 0)} />
      </div>

      <div className="ticket-risk-total">
        <span>
          {totalLabel}{" "}
          <strong className={isCredit ? "ticket-risk-cell-value--gain" : undefined}>
            {total == null ? DASH : `${usd(total)}${isCredit ? " CR" : " DR"}`}
          </strong>
        </span>
      </div>

      {geometry && (
        <div className="ticket-risk-payoff-wrap">
          <svg
            className="ticket-risk-payoff"
            viewBox={`0 0 ${CURVE_W} ${CURVE_H}`}
            preserveAspectRatio="none"
            role="img"
            aria-label="Profit and loss at expiry"
          >
            <line
              x1="0"
              y1={geometry.zeroY}
              x2={CURVE_W}
              y2={geometry.zeroY}
              stroke="var(--line-grid)"
              strokeWidth="1"
              vectorEffect="non-scaling-stroke"
            />
            {curve.breakevens.map((b) => (
              <line
                key={b}
                data-testid="payoff-breakeven"
                x1={geometry.toX(b)}
                y1="0"
                x2={geometry.toX(b)}
                y2={CURVE_H}
                stroke="var(--text-muted)"
                strokeDasharray="3 3"
                strokeWidth="1"
                vectorEffect="non-scaling-stroke"
              />
            ))}
            <polyline
              points={geometry.polyline}
              fill="none"
              stroke="var(--signal-core)"
              strokeWidth="1.6"
              vectorEffect="non-scaling-stroke"
            />
          </svg>
          <div className="ticket-risk-payoff-axis">
            {curve.breakevens.map((b) => (
              <span key={b}>{b.toFixed(2)}</span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
