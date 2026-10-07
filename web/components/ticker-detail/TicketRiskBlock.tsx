"use client";

import { useMemo } from "react";
import { payoffCurve, type PayoffLeg } from "@/lib/order/payoff";
import type { OrderPresentationSummary } from "@/lib/order/types";

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
  /** Whole-spread figures when held options cover this order (held leg at its basis). */
  withHeldLegs?: OrderPresentationSummary["withHeldLegs"];
};

const DASH = "---";

/**
 * Signed by default. A negative FUNDS AFTER means the order overdraws the
 * account; rendering it as `$12,000` made "short" and "spare" the same six
 * characters (R-279). Callers that genuinely want a magnitude — MAX GAIN,
 * MAX LOSS and the total, whose sign the label already carries — pass
 * `magnitude`.
 */
function usd(value: number | null, fractionDigits = 2, magnitude = false): string {
  if (value == null || !Number.isFinite(value)) return DASH;
  const shown = magnitude ? Math.abs(value) : value;
  const sign = shown < 0 ? "-" : "";
  return `${sign}$${Math.abs(shown).toLocaleString("en-US", {
    minimumFractionDigits: fractionDigits,
    maximumFractionDigits: fractionDigits,
  })}`;
}

/** Signed P&L: "+" for a gain, "-" for a loss, toned to match. */
function SignedCell({ label, value, unbounded = false }: { label: string; value: number | null; unbounded?: boolean }) {
  if (unbounded) return <Cell label={label} value="UNBOUNDED" tone="gain" />;
  const shown = usd(value);
  return (
    <Cell
      label={label}
      value={value != null && value > 0 ? `+${shown}` : shown}
      tone={value == null || value === 0 ? undefined : value > 0 ? "gain" : "loss"}
    />
  );
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
  withHeldLegs = null,
}: TicketRiskBlockProps) {
  const curve = useMemo(() => payoffCurve(legs, netPremium, { spot }), [legs, netPremium, spot]);

  const measuredBreakevens = curve.breakevens.filter(Number.isFinite);
  const breakevenLabel =
    measuredBreakevens.length === 0
      ? DASH
      : measuredBreakevens.map((b) => b.toFixed(2)).join(" / ");

  const geometry = useMemo(() => {
    if (!Number.isFinite(netPremium) || curve.points.length === 0
      || !curve.points.every(point => Number.isFinite(point.underlying) && Number.isFinite(point.pnl))) return null;
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
    const zeroY = toY(0);
    const points = curve.points.map(point => [toX(point.underlying), toY(point.pnl)]);
    if (![spanX, spanY, loY, zeroY, ...curve.breakevens.map(toX), ...points.flat()].every(Number.isFinite)) return null;
    return {
      toX,
      toY,
      zeroY,
      polyline: points.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" "),
    };
  }, [curve, netPremium]);

  return (
    <div className="ticket-risk" data-testid="ticket-risk">
      {/* The grid is fed from the WHOLE-ORDER risk summary; only the payoff
          curve below is per-combo. One heading claiming "PER 1× COMBO" over
          both was wrong for six of the cells (R-280). */}
      <div className="ticket-risk-head">
        <span>RISK · ORDER TOTAL</span>
      </div>

      <div className="ticket-risk-grid">
        {withHeldLegs != null ? (
          // Against a held leg the clamped magnitudes mislead: a credit wider
          // than the spread reads "MAX LOSS $0" when it locks in a gain.
          <>
            <SignedCell label="BEST CASE" value={withHeldLegs.orderBest} unbounded={withHeldLegs.bestUnbounded} />
            <SignedCell label="WORST CASE" value={withHeldLegs.orderWorst} />
          </>
        ) : (
          <>
            <Cell label="MAX GAIN" value={usd(maxGain, 2, true)} tone={maxGain != null ? "gain" : undefined} />
            <Cell
              label="MAX LOSS"
              value={maxLossUnbounded ? "UNBOUNDED" : usd(maxLoss, 2, true)}
              tone={maxLossUnbounded || maxLoss != null ? "loss" : undefined}
            />
          </>
        )}
        <Cell label="ORDER BREAKEVENS" value={breakevenLabel} />
        <Cell label="P(PROFIT)" value={DASH} />
        <Cell
          label="MARGIN REQ"
          value={usd(marginRequirement, 0, true)}
          tone={marginRequirement != null ? "warn" : undefined}
        />
        <Cell
          label="FUNDS AFTER"
          value={usd(fundsAfter, 0)}
          tone={fundsAfter != null && fundsAfter < 0 ? "loss" : undefined}
        />
      </div>

      {withHeldLegs != null && (
        <>
          {/* BEST / WORST CASE above price the held leg at $0 (already paid
              for). These price the resulting spread with that leg at its
              cost basis. Signed: a spread that loses everywhere has a
              negative best case. */}
          <div className="ticket-risk-head ticket-risk-head--spread" data-testid="ticket-risk-spread">
            <span>
              SPREAD · INCL. HELD LEG
              {withHeldLegs.heldBasisDollars != null
                ? ` @ ${usd(withHeldLegs.heldBasisDollars, 0)} BASIS`
                : " · BASIS UNKNOWN"}
            </span>
          </div>
          <div className="ticket-risk-grid">
            <SignedCell
              label="SPREAD BEST CASE"
              value={withHeldLegs.spreadBest}
              unbounded={withHeldLegs.bestUnbounded}
            />
            <SignedCell label="SPREAD WORST CASE" value={withHeldLegs.spreadWorst} />
          </div>
        </>
      )}

      <div className="ticket-risk-total">
        <span>
          {totalLabel}{" "}
          <strong className={isCredit ? "ticket-risk-cell-value--gain" : undefined}>
            {total == null ? DASH : `${usd(total, 2, true)}${isCredit ? " CR" : " DR"}`}
          </strong>
        </span>
      </div>

      {!geometry && <div className="ticket-risk-payoff-label" data-testid="payoff-unmeasured">Expiry payoff cannot be measured from the current price and legs.</div>}
      {geometry && (
        <div className="ticket-risk-payoff-wrap">
          {/* This curve and the BREAKEVENS cell are the per-combo figures. */}
          <div className="ticket-risk-payoff-label">AT EXPIRY · ORDER LEGS ONLY · PER 1× COMBO</div>
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
