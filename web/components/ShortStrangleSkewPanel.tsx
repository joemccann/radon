"use client";

import type { OrderLeg } from "@/lib/optionsChainUtils";
import type { PriceData } from "@/lib/pricesProtocol";
import { computeShortStrangleSkew } from "@/lib/shortStrangleSkew";

type ShortStrangleSkewPanelProps = {
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

export default function ShortStrangleSkewPanel({
  ticker,
  legs,
  prices,
  spot = null,
  riskFreeRate,
  compact = false,
}: ShortStrangleSkewPanelProps) {
  const skew = computeShortStrangleSkew({ ticker, legs, prices, spot, riskFreeRate });
  if (skew == null) return null;

  if (skew.status === "unavailable") {
    return (
      <div className={`short-strangle-skew${compact ? " short-strangle-skew--compact" : ""}`} data-testid="short-strangle-skew-panel">
        <div className="short-strangle-skew__header">
          <span>STRANGLE SKEW</span>
          <span>WAITING FOR WING MARKS</span>
        </div>
        <div className="short-strangle-skew__unavailable">
          {skew.reason === "missing-spot" ? "Spot unavailable." : "Need valid IV or bid/ask marks on both wings."}
        </div>
      </div>
    );
  }

  const netDeltaShares = skew.deltaSharesTotal;
  const shortCallDelta = -skew.call.delta;
  const shortPutDelta = -skew.put.delta;
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

  return (
    <div className={`short-strangle-skew${compact ? " short-strangle-skew--compact" : ""}`} data-testid="short-strangle-skew-panel">
      <div className="short-strangle-skew__header">
        <span>STRANGLE SKEW</span>
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
        <div className="short-strangle-skew__metric">
          <span className="short-strangle-skew__label">CALL Δ</span>
          <span className="short-strangle-skew__value short-strangle-skew__value--negative">
            {fmtDelta(shortCallDelta)}
          </span>
          <span className="short-strangle-skew__sub">SHORT LEG</span>
        </div>
        <div className="short-strangle-skew__metric">
          <span className="short-strangle-skew__label">PUT Δ</span>
          <span className="short-strangle-skew__value short-strangle-skew__value--positive">
            {fmtDelta(shortPutDelta)}
          </span>
          <span className="short-strangle-skew__sub">SHORT LEG</span>
        </div>
        <div className="short-strangle-skew__metric">
          <span className="short-strangle-skew__label">NET Δ</span>
          <span className={`short-strangle-skew__value short-strangle-skew__value--${deltaTone}`}>
            {fmtShares(netDeltaShares)}
          </span>
          <span className="short-strangle-skew__sub">
            {fmtDelta(skew.netDeltaPerCombo)} / combo
          </span>
        </div>
        <div className="short-strangle-skew__metric short-strangle-skew__metric--source">
          <span className="short-strangle-skew__label">SRC</span>
          <span className="short-strangle-skew__value">{sourceLabel}</span>
          <span className="short-strangle-skew__sub">{skewLabel}</span>
        </div>
      </div>
    </div>
  );
}
