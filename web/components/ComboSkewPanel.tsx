"use client";

import type { OrderLeg } from "@/lib/optionsChainUtils";
import type { PriceData } from "@/lib/pricesProtocol";
import { computeComboSkew, type ComboSkewLeg, type ComboSkewStructure } from "@/lib/comboSkew";

type ComboSkewPanelProps = {
  ticker: string;
  legs: OrderLeg[];
  prices: Record<string, PriceData>;
  spot?: number | null;
  riskFreeRate?: number;
  compact?: boolean;
};

function fmtIv(value: number): string {
  return `${(value * 100).toFixed(1)}%`;
}

function fmtVolPts(value: number): string {
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(1)} pt`;
}

function fmtDelta(value: number): string {
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(3)}`;
}

function fmtShares(value: number): string {
  const rounded = Math.round(value);
  const sign = rounded > 0 ? "+" : "";
  return `${sign}${rounded.toLocaleString()} sh`;
}

function fmtSource(source: "stream" | "mid" | "last" | "close"): string {
  if (source === "stream") return "STREAM";
  if (source === "mid") return "MID BS";
  if (source === "last") return "LAST BS";
  return "CLOSE BS";
}

function panelTitle(structure: ComboSkewStructure): string {
  return structure === "Risk Reversal" ? "RISK REVERSAL SKEW" : "STRANGLE SKEW";
}

function positionDelta(leg: ComboSkewLeg): number {
  return leg.action === "BUY" ? leg.delta : -leg.delta;
}

function legSubLabel(leg: ComboSkewLeg): string {
  return leg.action === "BUY" ? "LONG LEG" : "SHORT LEG";
}

function deltaToneClass(value: number): string {
  if (Math.abs(value) < 1e-9) return "neutral";
  return value > 0 ? "positive" : "negative";
}

export default function ComboSkewPanel({
  ticker,
  legs,
  prices,
  spot = null,
  riskFreeRate,
  compact = false,
}: ComboSkewPanelProps) {
  const skew = computeComboSkew({ ticker, legs, prices, spot, riskFreeRate });
  if (skew == null) return null;

  if (skew.status === "unavailable") {
    return (
      <div className={`short-strangle-skew${compact ? " short-strangle-skew--compact" : ""}`} data-testid="short-strangle-skew-panel">
        <div className="short-strangle-skew__header">
          <span>{panelTitle(skew.structure)}</span>
          <span>WAITING FOR WING MARKS</span>
        </div>
        <div className="short-strangle-skew__unavailable">
          {skew.reason === "missing-spot" ? "Spot unavailable." : "Need valid IV or bid/ask marks on both wings."}
        </div>
      </div>
    );
  }

  const netDeltaShares = skew.deltaSharesTotal;
  const callPositionDelta = positionDelta(skew.call);
  const putPositionDelta = positionDelta(skew.put);
  const deltaTone =
    Math.abs(netDeltaShares) < 0.5
      ? "neutral"
      : netDeltaShares < 0
        ? "negative"
        : "positive";
  const skewLabel =
    skew.skewSide === "FLAT"
      ? "FLAT"
      : `${skew.skewSide} RICH`;
  const sourceLabel =
    skew.call.source === skew.put.source
      ? fmtSource(skew.call.source)
      : "MIXED";

  // Compact (order ticket): four primary metrics. Full: add per-leg deltas + SRC.
  return (
    <div className={`short-strangle-skew${compact ? " short-strangle-skew--compact" : ""}`} data-testid="short-strangle-skew-panel">
      <div className="short-strangle-skew__header">
        <span>{panelTitle(skew.structure)}</span>
        <span>{sourceLabel}</span>
      </div>
      <div className="short-strangle-skew__grid">
        <div className="short-strangle-skew__metric">
          <span className="short-strangle-skew__label">CALL IV</span>
          <span className="short-strangle-skew__value">{fmtIv(skew.call.iv)}</span>
          <span className="short-strangle-skew__sub">${skew.call.strike}</span>
        </div>
        <div className="short-strangle-skew__metric">
          <span className="short-strangle-skew__label">PUT IV</span>
          <span className="short-strangle-skew__value">{fmtIv(skew.put.iv)}</span>
          <span className="short-strangle-skew__sub">${skew.put.strike}</span>
        </div>
        <div className="short-strangle-skew__metric">
          <span className="short-strangle-skew__label">IV SKEW</span>
          <span className={`short-strangle-skew__value short-strangle-skew__value--${skew.skewSide === "CALL" ? "positive" : skew.skewSide === "PUT" ? "negative" : "neutral"}`}>
            {fmtVolPts(skew.skewVolPts)}
          </span>
          <span className="short-strangle-skew__sub">{skewLabel}</span>
        </div>
        {!compact && (
          <>
            <div className="short-strangle-skew__metric">
              <span className="short-strangle-skew__label">CALL Δ</span>
              <span className={`short-strangle-skew__value short-strangle-skew__value--${deltaToneClass(callPositionDelta)}`}>
                {fmtDelta(callPositionDelta)}
              </span>
              <span className="short-strangle-skew__sub">{legSubLabel(skew.call)}</span>
            </div>
            <div className="short-strangle-skew__metric">
              <span className="short-strangle-skew__label">PUT Δ</span>
              <span className={`short-strangle-skew__value short-strangle-skew__value--${deltaToneClass(putPositionDelta)}`}>
                {fmtDelta(putPositionDelta)}
              </span>
              <span className="short-strangle-skew__sub">{legSubLabel(skew.put)}</span>
            </div>
          </>
        )}
        <div className="short-strangle-skew__metric">
          <span className="short-strangle-skew__label">NET Δ</span>
          <span className={`short-strangle-skew__value short-strangle-skew__value--${deltaTone}`}>
            {fmtShares(netDeltaShares)}
          </span>
          <span className="short-strangle-skew__sub">
            {fmtDelta(skew.netDeltaPerCombo)} / combo
          </span>
        </div>
        {!compact && (
          <div className="short-strangle-skew__metric short-strangle-skew__metric--source">
            <span className="short-strangle-skew__label">SRC</span>
            <span className="short-strangle-skew__value">{sourceLabel}</span>
            <span className="short-strangle-skew__sub">{skewLabel}</span>
          </div>
        )}
      </div>
    </div>
  );
}
