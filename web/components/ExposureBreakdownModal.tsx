"use client";

import { useState } from "react";
import MetricBreakdownModal, { type MetricBreakdownColumn } from "./MetricBreakdownModal";
import type { ExposureDataWithBreakdown, ExposureBreakdownRow } from "@/lib/exposureBreakdown";
import {
  computeLeverageRatio,
  classifyLeverageBias,
  formatLeveragePct,
  formatLeverageMultiplier,
} from "@/lib/dollarDeltaLeverage";
import { fmtUsd, fmtSignedUsd } from "@/lib/format/money";

export type ExposureMetric = "netLong" | "netShort" | "dollarDelta" | "netExposure";

type Props = {
  metric: ExposureMetric | null;
  exposure: ExposureDataWithBreakdown;
  bankroll: number;
  /** Net Liquidation Value — required for the delta-adjusted leverage block on the dollarDelta metric. */
  netLiquidation?: number;
  onClose: () => void;
};

const DOLLAR_DELTA_FORMULA =
  "Dollar Delta = SUM( position_delta x spot_price )\n" +
  "Leverage = Dollar Delta / Net Liquidation Value";

const METRIC_CONFIG: Record<ExposureMetric, {
  title: string;
  formula: string;
  contributionLabel: string;
  getValue: (e: ExposureDataWithBreakdown) => number;
  getContribution: (row: ExposureBreakdownRow) => number;
  formatValue: (n: number) => string;
}> = {
  netLong: {
    title: "Net Long Exposure",
    formula: "Net Long = SUM( |market_value| ) where position_delta > 0",
    contributionLabel: "MKT VALUE",
    getValue: (e) => e.netLong,
    getContribution: (r) => r.delta > 0 ? r.marketValue : 0,
    formatValue: fmtUsd,
  },
  netShort: {
    title: "Net Short Exposure",
    formula: "Net Short = SUM( |market_value| ) where position_delta < 0",
    contributionLabel: "MKT VALUE",
    getValue: (e) => e.netShort,
    getContribution: (r) => r.delta < 0 ? r.marketValue : 0,
    formatValue: fmtUsd,
  },
  dollarDelta: {
    title: "Dollar Delta",
    formula: DOLLAR_DELTA_FORMULA,
    contributionLabel: "$ DELTA",
    getValue: (e) => e.dollarDelta,
    getContribution: (r) => r.dollarDelta,
    formatValue: fmtSignedUsd,
  },
  netExposure: {
    title: "Net Exposure",
    formula: "Net Exposure % = ( Net_Long - Net_Short ) / Bankroll x 100",
    contributionLabel: "$ DELTA",
    getValue: (e) => e.netExposurePct,
    getContribution: (r) => r.dollarDelta,
    formatValue: (n) => `${n >= 0 ? "+" : ""}${n.toFixed(1)}%`,
  },
};

function fmtNlvUsd(n: number): string {
  return `$${n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function fmtDelta(n: number): string {
  return `${n >= 0 ? "+" : ""}${n.toFixed(0)}`;
}

function fmtSpot(n: number | null): string {
  if (n == null) return "---";
  return `$${n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function fmtLegDelta(n: number | null): string {
  if (n == null) return "---";
  return n >= 0 ? `+${n.toFixed(4)}` : n.toFixed(4);
}

export default function ExposureBreakdownModal({ metric, exposure, bankroll, netLiquidation, onClose }: Props) {
  const [expandedId, setExpandedId] = useState<number | null>(null);

  if (!metric) return null;

  const config = METRIC_CONFIG[metric];
  const totalValue = config.getValue(exposure);

  // Filter rows that contribute to this metric, sort by contribution magnitude
  const rows = exposure.rows
    .filter((r) => {
      if (metric === "netLong") return r.delta > 0;
      if (metric === "netShort") return r.delta < 0;
      return true; // dollarDelta and netExposure show all positions
    })
    .sort((a, b) => Math.abs(config.getContribution(b)) - Math.abs(config.getContribution(a)));

  const showLeverage = metric === "dollarDelta";
  const leverage = showLeverage && netLiquidation != null
    ? computeLeverageRatio(exposure.dollarDelta, netLiquidation)
    : null;

  const columns: MetricBreakdownColumn[] = [
    { header: "TICKER" },
    { header: "STRUCTURE" },
    { header: "SPOT" },
    { header: "DELTA" },
    { header: config.contributionLabel },
    { header: "SRC" },
  ];

  return (
    <MetricBreakdownModal
      open
      onClose={() => { setExpandedId(null); onClose(); }}
      title={config.title}
      className="exposure-breakdown-modal"
      value={config.formatValue(totalValue)}
      valueDetail={metric === "netExposure" ? (
        <>
          {fmtUsd(exposure.netLong)} long - {fmtUsd(exposure.netShort)} short / {fmtUsd(bankroll)} bankroll
        </>
      ) : undefined}
      beforeFormula={showLeverage && leverage ? (
        <LeverageBlock
          dollarDelta={exposure.dollarDelta}
          netLiquidation={netLiquidation as number}
          leverage={leverage}
          hasApprox={exposure.rows.some((r) => r.deltaSource !== "ib")}
        />
      ) : undefined}
      formula={config.formula}
      hasRows={rows.length > 0}
      emptyMessage="No positions contribute to this metric"
      tableHead={(
        <thead>
          <tr>
            {columns.map((col, i) => (
              <th key={i} className={col.className}>{col.header}</th>
            ))}
          </tr>
        </thead>
      )}
      tableBody={(
        <tbody>
          {rows.map((row) => {
            const isExpanded = expandedId === row.positionId;
            const contribution = config.getContribution(row);
            return (
              <RowGroup
                key={row.positionId}
                row={row}
                contribution={contribution}
                isExpanded={isExpanded}
                onToggle={() => setExpandedId(isExpanded ? null : row.positionId)}
                formatContribution={metric === "netLong" || metric === "netShort" ? fmtUsd : fmtSignedUsd}
              />
            );
          })}
        </tbody>
      )}
    />
  );
}

/* ─── Delta-adjusted leverage block ─────────────────────── */

function LeverageBlock({
  dollarDelta,
  netLiquidation,
  leverage,
  hasApprox,
}: {
  dollarDelta: number;
  netLiquidation: number;
  leverage: { pct: number; multiplier: number };
  hasApprox: boolean;
}) {
  const bias = classifyLeverageBias(leverage.pct);
  const biasLabel = bias === "long"
    ? "long-biased"
    : bias === "short"
      ? "short-biased"
      : "market-neutral";
  const exposurePerDollar = Math.abs(leverage.multiplier).toFixed(2);

  return (
    <div className={`dd-leverage-block dd-leverage-${bias}`} data-testid="dd-leverage-block">
      <div className="dd-leverage-row">
        <div className="dd-leverage-multiplier" data-testid="dd-leverage-multiplier">
          {formatLeverageMultiplier(leverage.multiplier)}
        </div>
        <div className="dd-leverage-meta">
          <span className="dd-leverage-pct" data-testid="dd-leverage-pct">
            {formatLeveragePct(leverage.pct)}
          </span>
          <span className="dd-leverage-divider" aria-hidden="true">/</span>
          <span className="dd-leverage-bias" data-testid="dd-leverage-bias">
            {biasLabel}
          </span>
        </div>
      </div>
      <div className="dd-leverage-interpretation" data-testid="dd-leverage-interpretation">
        Every $1 of NLV moves with ${exposurePerDollar} of directional exposure.
      </div>
      <div className="dd-leverage-footnote">
        <span className="dd-leverage-nlv" data-testid="dd-leverage-nlv">
          NLV {fmtNlvUsd(netLiquidation)}
        </span>
        <span className="dd-leverage-dollar-delta" aria-hidden="true">
          {" "}/{" "}$ Delta {fmtSignedUsd(dollarDelta)}
        </span>
        {hasApprox && (
          <span className="dd-leverage-approx" data-testid="dd-leverage-approx">
            includes APPROX legs
          </span>
        )}
      </div>
    </div>
  );
}

/* ─── Per-position row with expandable legs ─────────────── */

function RowGroup({
  row,
  contribution,
  isExpanded,
  onToggle,
  formatContribution,
}: {
  row: ExposureBreakdownRow;
  contribution: number;
  isExpanded: boolean;
  onToggle: () => void;
  formatContribution: (n: number) => string;
}) {
  const hasLegs = row.legs.length > 1;

  return (
    <>
      <tr className="eb-row" onClick={hasLegs ? onToggle : undefined} style={hasLegs ? { cursor: "pointer" } : undefined}>
        <td className="eb-ticker">
          {hasLegs && <span className="eb-expand">{isExpanded ? "▼" : "▶"}</span>}
          {row.ticker}
        </td>
        <td className="eb-structure" title={row.structure}>{row.structure}</td>
        <td className="eb-mono">{fmtSpot(row.spot)}</td>
        <td className="eb-mono">{fmtDelta(row.delta)}</td>
        <td className="eb-mono">{formatContribution(contribution)}</td>
        <td><span className={`eb-source eb-source-${row.deltaSource}`}>{row.deltaSource.toUpperCase()}</span></td>
      </tr>
      {isExpanded && row.legs.map((leg, i) => (
        <tr key={i} className="eb-leg-row">
          <td></td>
          <td className="eb-leg-detail">
            {leg.direction} {leg.contracts}x {leg.type}{leg.strike ? ` $${leg.strike}` : ""}
          </td>
          <td></td>
          <td className="eb-mono eb-leg-delta">{fmtLegDelta(leg.rawDelta)}</td>
          <td className="eb-mono eb-leg-delta">{fmtDelta(leg.legDelta)}</td>
          <td></td>
        </tr>
      ))}
    </>
  );
}
