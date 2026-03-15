"use client";

import { useState } from "react";
import Modal from "./Modal";
import type { ExposureDataWithBreakdown, ExposureBreakdownRow } from "@/lib/exposureBreakdown";

export type ExposureMetric = "netLong" | "netShort" | "dollarDelta" | "netExposure";

type Props = {
  metric: ExposureMetric | null;
  exposure: ExposureDataWithBreakdown;
  bankroll: number;
  onClose: () => void;
};

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
    formula: "Dollar Delta = SUM( position_delta x spot_price )",
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

function fmtUsd(n: number): string {
  if (Math.abs(n) >= 1_000_000) return `$${(n / 1_000_000).toFixed(2)}M`;
  return `$${n.toLocaleString("en-US", { maximumFractionDigits: 0 })}`;
}

function fmtSignedUsd(n: number): string {
  return `${n >= 0 ? "+" : ""}${fmtUsd(Math.abs(n))}${n < 0 ? "" : ""}`.replace("+-", "-");
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

export default function ExposureBreakdownModal({ metric, exposure, bankroll, onClose }: Props) {
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

  return (
    <Modal
      open
      onClose={() => { setExpandedId(null); onClose(); }}
      title={config.title}
      className="exposure-breakdown-modal"
    >
      {/* Total value */}
      <div className="eb-total">
        <span className="eb-total-value">{config.formatValue(totalValue)}</span>
        {metric === "netExposure" && (
          <span className="eb-total-detail">
            {fmtUsd(exposure.netLong)} long - {fmtUsd(exposure.netShort)} short / {fmtUsd(bankroll)} bankroll
          </span>
        )}
      </div>

      {/* Formula */}
      <div className="eb-formula">
        <code>{config.formula}</code>
      </div>

      {/* Per-position table */}
      {rows.length > 0 ? (
        <table className="eb-table">
          <thead>
            <tr>
              <th>TICKER</th>
              <th>STRUCTURE</th>
              <th>SPOT</th>
              <th>DELTA</th>
              <th>{config.contributionLabel}</th>
              <th>SRC</th>
            </tr>
          </thead>
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
        </table>
      ) : (
        <div className="eb-empty">No positions contribute to this metric</div>
      )}
    </Modal>
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
          {hasLegs && <span className="eb-expand">{isExpanded ? "\u25BC" : "\u25B6"}</span>}
          {row.ticker}
        </td>
        <td className="eb-structure">{row.structure}</td>
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
