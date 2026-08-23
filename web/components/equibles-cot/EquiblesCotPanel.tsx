"use client";

import { useMemo, useState } from "react";
import { Scale } from "lucide-react";
import BrushMinimap from "../BrushMinimap";
import CriHistoryChart, { type ChartSeries } from "../CriHistoryChart";
import InfoTooltip from "../InfoTooltip";
import MetricCell from "../mobile/MetricCell";
import SectionEmptyState from "../SectionEmptyState";
import SpectralLoader from "../SpectralLoader";
import { RegimeStrip, RegimeStripCell } from "../RegimeStrip";
import { chartSeriesColor } from "@/lib/chartSystem";
import { COT_POSITIONING_REFRESH, dataAgeDays, nextRefreshLabel } from "@/lib/refreshSchedule";
import { useViewport } from "@/lib/useViewport";
import {
  CONTRARIAN_TONE,
  COT_RANGE_PRESETS,
  buildCotChartRows,
  cotPresetRange,
  crowdingColor,
  formatContracts,
  formatPercentile,
  formatReportDate,
  formatReportFreshness,
  formatShareOfOi,
  formatZScore,
  selectContract,
  type CotBoardRow,
  type CotChartRow,
  type CotContract,
  type CotRangeSlug,
} from "./cotPositioning";
import { useEquiblesCot } from "./useEquiblesCot";
import SortTh from "../SortTh";
import { useSort } from "@/lib/useSort";

type CotBoardSortKey = "contract" | "category" | "spec" | "comm" | "oi" | "reported";

function cotBoardExtract(row: CotBoardRow, key: CotBoardSortKey): string | number | null {
  switch (key) {
    case "contract": return row.name ?? row.market_code;
    case "category": return row.category;
    case "spec": return row.net_noncommercial;
    case "comm": return row.net_commercial;
    case "oi": return row.net_noncommercial_pct_oi;
    case "reported": return row.report_date;
    default: return null;
  }
}

function CotBoardTable({ rows }: { rows: CotBoardRow[] }) {
  const { sorted, sort, toggle } = useSort(rows, cotBoardExtract);
  return (
    <table className="data-table">
      <thead>
        <tr>
          <SortTh<CotBoardSortKey> label="CONTRACT" sortKey="contract" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
          <SortTh<CotBoardSortKey> label="CATEGORY" sortKey="category" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
          <SortTh<CotBoardSortKey> label="NET SPEC" sortKey="spec" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
          <SortTh<CotBoardSortKey> label="NET COMM" sortKey="comm" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
          <SortTh<CotBoardSortKey> label="% OI" sortKey="oi" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
          <SortTh<CotBoardSortKey> label="REPORTED" sortKey="reported" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
        </tr>
      </thead>
      <tbody>
        {sorted.map((row) => (
          <tr key={`${row.market_code}-${row.report_date}`}>
            <td>{row.name ?? row.market_code}</td>
            <td>{row.category ?? "---"}</td>
            <td style={{ textAlign: "right" }}>{formatContracts(row.net_noncommercial)}</td>
            <td style={{ textAlign: "right" }}>{formatContracts(row.net_commercial)}</td>
            <td style={{ textAlign: "right" }}>{formatShareOfOi(row.net_noncommercial_pct_oi)}</td>
            <td style={{ textAlign: "right" }}>{formatReportDate(row.report_date)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

const DEFAULT_LOOKBACK_DAYS = 365 * 3;
const DEFAULT_MIN_STATS_WEEKS = 52;

function contractLabel(contract: CotContract): string {
  return contract.name ?? contract.alias;
}

function methodologyFootnote(
  reportDate: string | null,
  lookbackDays: number,
  minWeeks: number,
  extremes: { high: number; low: number },
): string {
  const years = Math.round(lookbackDays / 365);
  const age = dataAgeDays(reportDate);
  return (
    `CFTC Commitments of Traders, week ending ${formatReportDate(reportDate)}` +
    `${age != null ? ` (${age}d old)` : ""}. ` +
    `CFTC publishes each Tuesday's positions on Friday afternoon ET. ` +
    `Percentile ranks the speculative net against its own trailing ${years}y of weekly reports ` +
    `(withheld under ${minWeeks} reports). Above ${extremes.high} or below ${extremes.low} is a ` +
    `crowded book, and crowded books are read contrarian. ` +
    `Next refresh ${nextRefreshLabel(COT_POSITIONING_REFRESH)}.`
  );
}

export default function EquiblesCotPanel() {
  const { data, loading, syncing, lastSync } = useEquiblesCot();
  const { isMobile, hasMounted } = useViewport();
  const compact = hasMounted && isMobile;

  const [activeAlias, setActiveAlias] = useState<string>("");
  const [activeRange, setActiveRange] = useState<CotRangeSlug | "custom">("3y");
  const [customRange, setCustomRange] = useState<[number, number] | null>(null);

  const contracts = data?.contracts ?? [];
  const contract = selectContract(contracts, activeAlias);
  const total = contract?.series.length ?? 0;

  const chartRange = useMemo<[number, number]>(() => {
    if (total < 2) return [0, Math.max(total - 1, 0)];
    if (activeRange === "custom" && customRange) {
      const max = total - 1;
      const end = Math.min(customRange[1], max);
      return [Math.max(0, Math.min(customRange[0], end)), end];
    }
    return cotPresetRange(activeRange === "custom" ? "all" : activeRange, total);
  }, [activeRange, customRange, total]);

  if ((loading || syncing) && !data) {
    return <SpectralLoader label="Sampling CFTC speculator positioning" />;
  }

  if (!data || data.missing || !contract) {
    return (
      <SectionEmptyState
        icon={Scale}
        headline="No COT positioning yet"
        secondary="The COT refresh timer populates this tab from the CFTC Commitments of Traders report. Data appears after the first successful pull."
      />
    );
  }

  const rows = buildCotChartRows(contract.series);
  const [start, end] = chartRange;
  const slice = rows.slice(start, end + 1);

  const chartSeries: [ChartSeries<CotChartRow>, ChartSeries<CotChartRow>] = [
    {
      key: "spec",
      label: "NET SPECULATOR",
      color: chartSeriesColor("primary"),
      axis: "left",
      scaleType: "linear",
      format: formatContracts,
    },
    {
      key: "comm",
      label: "NET COMMERCIAL",
      color: chartSeriesColor("comparison"),
      axis: "right",
      scaleType: "linear",
      format: formatContracts,
    },
  ];

  const freshness = formatReportFreshness(contract.report_date, new Date());
  const biasTone = CONTRARIAN_TONE[contract.contrarian_bias];

  return (
    <>
      {/* ── Summary strip ─────────────────────────────────── */}
      <div className="section">
        <div className="section-header">
          <div className="section-title">
            <Scale size={14} />
            CFTC Positioning
            <InfoTooltip text="Net non-commercial (speculator) futures positioning against its own trailing history, alongside the commercial hedgers on the other side. Positioning extremes are contrarian: a speculative net pinned at the top of its own range means the trade is crowded and the marginal buyer is gone." />
          </div>
          {lastSync && (
            <span style={{ fontFamily: "var(--font-mono)", fontSize: "9px", color: "var(--text-muted)" }}>
              {new Date(lastSync).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" })}
            </span>
          )}
        </div>

        {compact ? (
          <div className="m-regime-grid2x2" data-testid="cot-mobile-grid">
            <MetricCell label="NET SPEC" value={formatContracts(contract.net_noncommercial)} />
            <MetricCell label="PERCENTILE" value={formatPercentile(contract.percentile)} />
            <MetricCell label="CROWDING" value={contract.crowding} />
            <MetricCell label="REPORTED" value={formatReportDate(contract.report_date)} />
          </div>
        ) : (
          <RegimeStrip>
            <RegimeStripCell
              testId="cot-strip-net"
              label="NET SPECULATOR"
              value={formatContracts(contract.net_noncommercial)}
              sub={<>{formatShareOfOi(contract.net_noncommercial_pct_oi)} OF OPEN INTEREST</>}
            />
            <RegimeStripCell
              testId="cot-strip-percentile"
              label="PERCENTILE"
              value={formatPercentile(contract.percentile)}
              sub={<>Z {formatZScore(contract.z_score)} OVER {contract.weeks} REPORTS</>}
            />
            <RegimeStripCell
              testId="cot-strip-crowding"
              label="CROWDING"
              value={
                <span data-testid="cot-crowding-value" style={{ color: crowdingColor(contract.crowding) }}>
                  {contract.crowding}
                </span>
              }
              sub={
                <span data-testid="cot-contrarian-bias" style={{ color: biasTone }}>
                  READS {contract.contrarian_bias}
                </span>
              }
            />
            <RegimeStripCell
              testId="cot-strip-reported"
              label="REPORTED"
              value={formatReportDate(contract.report_date)}
              sub={<>{freshness}</>}
            />
          </RegimeStrip>
        )}
      </div>

      {/* ── Speculators vs hedgers chart ───────────────────── */}
      <div className="breadth-history-block" data-testid="cot-chart-section">
        <nav className="history-range-chips" aria-label="COT contract" data-testid="cot-contract-chips">
          {contracts.map((entry) => (
            <button
              key={entry.alias}
              type="button"
              className={`history-range-chip${entry.alias === contract.alias ? " is-active" : ""}`}
              aria-pressed={entry.alias === contract.alias}
              onClick={() => {
                setCustomRange(null);
                setActiveAlias(entry.alias);
              }}
            >
              {entry.alias}
            </button>
          ))}
        </nav>

        <nav className="history-range-chips" aria-label="COT chart range" data-testid="cot-range-chips">
          {COT_RANGE_PRESETS.filter((p) => p.weeks <= total || p.slug === "all").map((preset) => (
            <button
              key={preset.slug}
              type="button"
              className={`history-range-chip${activeRange === preset.slug ? " is-active" : ""}`}
              aria-pressed={activeRange === preset.slug}
              onClick={() => {
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

        <CriHistoryChart
          history={slice}
          series={chartSeries}
          title={`${contractLabel(contract)} · SPECULATORS VS HEDGERS`}
          xTickFormat={(d: Date) => d.toLocaleDateString("en-GB", { day: "2-digit", month: "short", timeZone: "UTC" })}
        />
        {total >= 2 && (
          <BrushMinimap
            values={contract.series.map((week) => week.net_noncommercial ?? 0)}
            range={chartRange}
            onRangeChange={(r) => setCustomRange(r)}
            onCustom={() => setActiveRange("custom")}
            testIdPrefix="cot-brush"
            ariaLabel="COT positioning history range brush"
          />
        )}
        <div
          data-testid="cot-methodnote"
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: "9px",
            color: "var(--text-muted)",
            lineHeight: "1.7",
            marginTop: "8px",
          }}
        >
          {methodologyFootnote(
            contract.report_date,
            data.lookback_days ?? DEFAULT_LOOKBACK_DAYS,
            data.min_stats_weeks ?? DEFAULT_MIN_STATS_WEEKS,
            data.extremes ?? { high: 90, low: 10 },
          )}
        </div>
      </div>

      {/* ── Cross-asset latest-week board ─────────────────── */}
      {data.market.length > 0 && (
        <div className="section" data-testid="cot-board">
          <div className="section-header">
            <div className="section-title">Cross-Asset Board</div>
          </div>
          <CotBoardTable rows={data.market} />
        </div>
      )}
    </>
  );
}
