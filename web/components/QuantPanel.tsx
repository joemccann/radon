"use client";

import { useMemo, useState } from "react";
import { Sigma } from "lucide-react";
import BrushMinimap from "./BrushMinimap";
import CriHistoryChart, { type ChartSeries } from "./CriHistoryChart";
import FreshnessRail from "./FreshnessRail";
import InfoTooltip from "./InfoTooltip";
import MetricCell from "./mobile/MetricCell";
import SectionEmptyState from "./SectionEmptyState";
import SortTh from "./SortTh";
import SpectralLoader from "./SpectralLoader";
import { RegimeStrip, RegimeStripCell } from "./RegimeStrip";
import { chartSeriesColor } from "@/lib/chartSystem";
import {
  DEFAULT_QUANT_STRATEGY,
  QUANT_RANGE_PRESETS,
  QUANT_STRATEGIES,
  buildEquityRows,
  estimatedShares,
  formatPct,
  formatPrice,
  formatRatio,
  formatSignedPct,
  formatUsd,
  formatWeight,
  grossNotional,
  quantLabel,
  quantPresetRange,
  type QuantEquityRow,
  type QuantOrderRow,
  type QuantRangeSlug,
  type QuantStrategyKey,
  type QuantTargetRow,
} from "@/lib/quant";
import { QUANT_TARGETS_REFRESH } from "@/lib/refreshSchedule";
import { useQuant } from "@/lib/useQuant";
import { useSort } from "@/lib/useSort";
import { useViewport } from "@/lib/useViewport";

const FOOTNOTE =
  "The quant sleeve runs outside the Four Gates: no flow signal, no convexity check, no Kelly ticket. Weights come from the registered rule on daily closes. Orders place only when RADON_QUANT_EXECUTE=1 is set on the execution timer; otherwise the plan is logged and nothing is sent.";

type TargetRow = QuantTargetRow & { shares: number | null };
type TargetSortKey = "symbol" | "weight" | "notional" | "close" | "shares";
type OrderSortKey = "action" | "quantity" | "symbol" | "limit_price" | "status" | "message";

function targetSortValue(row: TargetRow, key: TargetSortKey): string | number | null {
  return row[key];
}

function orderSortValue(row: QuantOrderRow, key: OrderSortKey): string | number | null {
  return row[key];
}

function isPresetSlug(slug: string): slug is QuantRangeSlug {
  return QUANT_RANGE_PRESETS.some((p) => p.slug === slug);
}

function drawdownTone(v: number | null | undefined): "pos" | "neg" | "warn" | "mut" {
  if (v == null || !Number.isFinite(v)) return "mut";
  if (v <= -0.2) return "neg";
  if (v <= -0.1) return "warn";
  return "pos";
}

export default function QuantPanel() {
  const [strategy, setStrategy] = useState<QuantStrategyKey>(DEFAULT_QUANT_STRATEGY);

  const chips = (
    <nav className="history-range-chips" aria-label="Quant strategy" data-testid="quant-strategy-chips">
      {QUANT_STRATEGIES.map((s) => (
        <button
          key={s.key}
          type="button"
          className={`history-range-chip${strategy === s.key ? " is-active" : ""}`}
          aria-pressed={strategy === s.key}
          onClick={() => setStrategy(s.key)}
        >
          {s.label}
        </button>
      ))}
    </nav>
  );

  // Keyed on the strategy so the hook, the range and the sort state remount
  // per selection instead of carrying one strategy's state into another's.
  return (
    <>
      {chips}
      <QuantStrategyView key={strategy} strategy={strategy} />
    </>
  );
}

function QuantStrategyView({ strategy }: { strategy: QuantStrategyKey }) {
  const { data, loading, syncing } = useQuant(strategy);
  const { isMobile, hasMounted } = useViewport();
  const compact = hasMounted && isMobile;

  // Multi-year equity curve: default to the full series like the other
  // long-history regime charts.
  const [activeRange, setActiveRange] = useState<QuantRangeSlug | "custom">("all");
  const [customRange, setCustomRange] = useState<[number, number] | null>(null);

  const run = data?.run ?? null;
  const targets = data?.targets ?? null;
  const orders = data?.orders ?? null;

  const equityRows = useMemo(() => buildEquityRows(run), [run]);
  const total = equityRows.length;

  const chartRange = useMemo<[number, number]>(() => {
    if (total < 2) return [0, Math.max(total - 1, 0)];
    if (activeRange === "custom" && customRange) {
      const max = total - 1;
      const end = Math.min(customRange[1], max);
      const start = Math.max(0, Math.min(customRange[0], end));
      return [start, end];
    }
    return quantPresetRange(activeRange === "custom" ? "all" : activeRange, total);
  }, [activeRange, customRange, total]);

  const targetRows = useMemo<TargetRow[]>(
    () => (targets?.rows ?? []).map((row) => ({ ...row, shares: estimatedShares(row) })),
    [targets],
  );
  const targetSort = useSort<TargetRow, TargetSortKey>(targetRows, targetSortValue, "weight", "desc");
  const orderRows = useMemo<QuantOrderRow[]>(() => orders?.rows ?? [], [orders]);
  const orderSort = useSort<QuantOrderRow, OrderSortKey>(orderRows, orderSortValue, "symbol");

  const label = quantLabel(strategy);

  if ((loading || syncing) && !data) {
    return <SpectralLoader label="Loading quant sleeve" />;
  }

  if (!data || data.missing || !run) {
    return (
      <SectionEmptyState
        icon={Sigma}
        headline="No quant sleeve runs yet"
        secondary={`No backtest has been persisted for ${label}. Run scripts/quant_run.py --persist for a backtest; the quant-targets timer writes the live target weights.`}
      />
    );
  }

  const m = run.metrics;
  const asOf = targets?.as_of ?? run.until;
  const rebalanceCopy = targets ? (targets.rebalance_today ? "REBALANCE TODAY" : "NO REBALANCE") : "NO TARGETS YET";
  const [start, end] = chartRange;
  const slice = equityRows.slice(start, end + 1);

  const chartSeries: [ChartSeries<QuantEquityRow>, ChartSeries<QuantEquityRow>] = [
    {
      key: "equity",
      label: "EQUITY",
      color: chartSeriesColor("primary"),
      axis: "left",
      scaleType: "log",
      format: (v: number) => formatUsd(v),
    },
    {
      key: "drawdown",
      label: "DRAWDOWN",
      color: chartSeriesColor("fault"),
      axis: "right",
      scaleType: "linear",
      format: (v: number) => formatSignedPct(v),
    },
  ];

  return (
    <>
      {/* ── Summary strip ─────────────────────────────────── */}
      <div className="section">
        <div className="section-header">
          <div className="section-title">
            <Sigma size={14} />
            Quant Sleeve
            <InfoTooltip text="Systematic strategies ported from the quants.md library, run on Radon's daily closes as a sleeve separate from the gated flow book. The strip reads the latest persisted backtest; the targets table is the current rule output. Sharpe is raw, with no risk-free deduction." />
          </div>
        </div>

        {compact ? (
          <div className="m-regime-grid2x2" data-testid="quant-mobile-grid">
            <MetricCell label="ANN RETURN" value={formatSignedPct(m.annualized_return)} />
            <MetricCell label="SHARPE" value={formatRatio(m.sharpe)} />
            <MetricCell label="MAX DD" value={formatSignedPct(m.max_drawdown)} tone={drawdownTone(m.max_drawdown)} />
            <MetricCell label="AS OF" value={asOf ?? "---"} />
          </div>
        ) : (
          <RegimeStrip className="regime-strip--fit">
            <RegimeStripCell
              testId="quant-strip-return"
              label="ANN RETURN"
              value={formatSignedPct(m.annualized_return)}
              sub={
                <>
                  {run.n_sessions} SESSIONS {" · "} {run.n_rebalances} REBALANCES
                </>
              }
            />
            <RegimeStripCell
              testId="quant-strip-sharpe"
              label="SHARPE"
              value={formatRatio(m.sharpe)}
              sub={<>SORTINO {formatRatio(m.sortino)}</>}
            />
            <RegimeStripCell
              testId="quant-strip-maxdd"
              label="MAX DD"
              value={formatSignedPct(m.max_drawdown)}
              sub={<>CALMAR {formatRatio(m.calmar)}</>}
            />
            <RegimeStripCell
              testId="quant-strip-vol"
              label="ANN VOL"
              value={formatPct(m.ann_vol)}
              sub={<>COST {run.cost_bps ?? "---"} BPS PER SIDE</>}
            />
            <RegimeStripCell
              testId="quant-strip-asof"
              label="AS OF"
              value={asOf ?? "---"}
              sub={<span data-testid="quant-rebalance-flag">{rebalanceCopy}</span>}
            />
          </RegimeStrip>
        )}

        <FreshnessRail
          schedule={QUANT_TARGETS_REFRESH}
          asOf={asOf}
          testId="quant-freshness-rail"
          asOfTestId="quant-strip-asof-rail"
        />
      </div>

      {/* ── Equity curve ──────────────────────────────────── */}
      <div className="breadth-history-block" data-testid="quant-chart-section">
        <nav className="history-range-chips" aria-label="Quant equity range" data-testid="quant-range-chips">
          {QUANT_RANGE_PRESETS.filter((p) => p.sessions <= total || p.slug === "all").map((preset) => (
            <button
              key={preset.slug}
              type="button"
              className={`history-range-chip${activeRange === preset.slug ? " is-active" : ""}`}
              aria-pressed={activeRange === preset.slug}
              onClick={() => {
                if (!isPresetSlug(preset.slug)) return;
                setCustomRange(null);
                setActiveRange(preset.slug);
              }}
            >
              {preset.label}
            </button>
          ))}
          {activeRange === "custom" ? (
            <span className="history-range-chip is-active history-range-chip-custom" aria-label="Custom range from brush selection">
              Custom
            </span>
          ) : null}
        </nav>

        <CriHistoryChart history={slice} series={chartSeries} title={`QUANT SLEEVE EQUITY ${label.toUpperCase()}`} />
        {total >= 2 && (
          <BrushMinimap
            values={equityRows.map((r) => r.equity)}
            range={chartRange}
            onRangeChange={(r) => setCustomRange(r)}
            onCustom={() => setActiveRange("custom")}
            testIdPrefix="quant-brush"
            ariaLabel="Quant equity history range brush"
          />
        )}
      </div>

      {/* ── Targets ───────────────────────────────────────── */}
      <div className="section" data-testid="quant-targets-section">
        <div className="section-header">
          <div className="section-title">
            Targets
            <InfoTooltip text="The rule's current target weight per symbol on the latest session, with the sleeve notional each weight maps to and the whole-share estimate at that session's close. Shares are an estimate; the execution bridge sizes off the close it trades against." />
          </div>
          <div className="section-meta" data-testid="quant-targets-gross">
            GROSS {formatUsd(grossNotional(targetRows))}
          </div>
        </div>
        {targetRows.length === 0 ? (
          <div className="section-body">
            <div className="snapshot-card__empty">No target weights have been written for this strategy yet.</div>
          </div>
        ) : (
          <div className="section-body table-wrap">
            <table data-testid="quant-targets-table">
              <thead>
                <tr>
                  <SortTh<TargetSortKey> label="Symbol" sortKey="symbol" activeKey={targetSort.sort.key} direction={targetSort.sort.direction} onToggle={targetSort.toggle} />
                  <SortTh<TargetSortKey> label="Weight" sortKey="weight" activeKey={targetSort.sort.key} direction={targetSort.sort.direction} onToggle={targetSort.toggle} className="right" />
                  <SortTh<TargetSortKey> label="Notional" sortKey="notional" activeKey={targetSort.sort.key} direction={targetSort.sort.direction} onToggle={targetSort.toggle} className="right" />
                  <SortTh<TargetSortKey> label="Close" sortKey="close" activeKey={targetSort.sort.key} direction={targetSort.sort.direction} onToggle={targetSort.toggle} className="right" />
                  <SortTh<TargetSortKey> label="Shares" sortKey="shares" activeKey={targetSort.sort.key} direction={targetSort.sort.direction} onToggle={targetSort.toggle} className="right" />
                </tr>
              </thead>
              <tbody>
                {targetSort.sorted.map((row) => (
                  <tr key={row.symbol} data-testid={`quant-target-${row.symbol}`}>
                    <td className="mono">{row.symbol}</td>
                    <td className="right mono">{formatWeight(row.weight)}</td>
                    <td className="right mono">{formatUsd(row.notional)}</td>
                    <td className="right mono">{formatPrice(row.close)}</td>
                    <td className="right mono">{row.shares == null ? "---" : row.shares.toLocaleString("en-US")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* ── Last execution plan ───────────────────────────── */}
      {orders && orderRows.length > 0 ? (
        <div className="section" data-testid="quant-orders-section">
          <div className="section-header">
            <div className="section-title">
              Execution {orders.as_of}
              <InfoTooltip text="What the execution bridge planned or placed for the latest rebalance session. A planned status means the run was a dry run and nothing reached the broker." />
            </div>
          </div>
          <div className="section-body table-wrap">
            <table data-testid="quant-orders-table">
              <thead>
                <tr>
                  <SortTh<OrderSortKey> label="Action" sortKey="action" activeKey={orderSort.sort.key} direction={orderSort.sort.direction} onToggle={orderSort.toggle} />
                  <SortTh<OrderSortKey> label="Qty" sortKey="quantity" activeKey={orderSort.sort.key} direction={orderSort.sort.direction} onToggle={orderSort.toggle} className="right" />
                  <SortTh<OrderSortKey> label="Symbol" sortKey="symbol" activeKey={orderSort.sort.key} direction={orderSort.sort.direction} onToggle={orderSort.toggle} />
                  <SortTh<OrderSortKey> label="Limit" sortKey="limit_price" activeKey={orderSort.sort.key} direction={orderSort.sort.direction} onToggle={orderSort.toggle} className="right" />
                  <SortTh<OrderSortKey> label="Status" sortKey="status" activeKey={orderSort.sort.key} direction={orderSort.sort.direction} onToggle={orderSort.toggle} />
                  <SortTh<OrderSortKey> label="Message" sortKey="message" activeKey={orderSort.sort.key} direction={orderSort.sort.direction} onToggle={orderSort.toggle} />
                </tr>
              </thead>
              <tbody>
                {orderSort.sorted.map((row, i) => (
                  <tr key={`${row.symbol}-${i}`}>
                    <td className="mono">{row.action}</td>
                    <td className="right mono">{row.quantity.toLocaleString("en-US")}</td>
                    <td className="mono">{row.symbol}</td>
                    <td className="right mono">{formatPrice(row.limit_price)}</td>
                    <td className="mono">{row.status.toUpperCase()}</td>
                    <td>{row.message ?? ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}

      <div className="section-footnote">{FOOTNOTE}</div>
    </>
  );
}
