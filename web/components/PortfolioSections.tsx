"use client";

import { useCallback, useMemo } from "react";
import { CheckCircle2, Circle, TriangleAlert } from "lucide-react";
import type { PortfolioData, PortfolioPosition } from "@/lib/types";
import type { PriceData } from "@/lib/pricesProtocol";
import { useColumnVisibility } from "@/lib/useColumnVisibility";
import { useTableFilter } from "@/lib/useTableFilter";
import { SECTION_TOOLTIPS } from "@/lib/sectionTooltips";
import { ColumnsToggle, type ColumnsToggleEntry } from "./ColumnsToggle";
import InfoTooltip from "./InfoTooltip";
import PositionTable, {
  POSITION_COLUMNS,
  POSITION_COLUMN_DEFAULTS,
  type PositionToggleableColumnKey,
} from "./PositionTable";
import TableSearch from "./TableSearch";

export type PortfolioSectionsProps = {
  portfolio: PortfolioData | null;
  prices?: Record<string, PriceData>;
};

function positionColumnEntries(hasOptions: boolean, hasExpiry: boolean) {
  let columns = POSITION_COLUMNS as readonly ColumnsToggleEntry<PositionToggleableColumnKey>[];
  if (!hasOptions) {
    columns = columns.filter((column) => column.key !== "implied" && column.key !== "implied_market_value");
  }
  if (!hasExpiry) {
    columns = columns.filter((column) => column.key !== "expiry");
  }
  return columns;
}

/** Route-local surface so /portfolio does not download every workspace route. */
export default function PortfolioSections({ portfolio, prices }: PortfolioSectionsProps) {
  const positions = portfolio?.positions ?? [];
  const definedPositions = positions.filter((p) => p.risk_profile === "defined");
  const equityPositions = positions.filter((p) => p.risk_profile === "equity");
  const undefinedPositions = positions.filter((p) => p.risk_profile === "undefined" || p.risk_profile === "complex");

  const extractPositionSearchText = useCallback(
    (p: PortfolioPosition) => `${p.ticker} ${p.structure} ${p.direction} ${p.expiry}`,
    [],
  );
  const definedFilter = useTableFilter(definedPositions, extractPositionSearchText);
  const undefinedFilter = useTableFilter(undefinedPositions, extractPositionSearchText);
  const equityFilter = useTableFilter(equityPositions, extractPositionSearchText);

  const definedCols = useColumnVisibility<PositionToggleableColumnKey>("positions-defined", POSITION_COLUMN_DEFAULTS);
  const undefinedCols = useColumnVisibility<PositionToggleableColumnKey>("positions-undefined", POSITION_COLUMN_DEFAULTS);
  const equityCols = useColumnVisibility<PositionToggleableColumnKey>("positions-equity", POSITION_COLUMN_DEFAULTS);

  const definedHasOptions = useMemo(
    () => definedFilter.filtered.some((p) => p.structure_type !== "Stock"),
    [definedFilter.filtered],
  );
  const undefinedHasOptions = useMemo(
    () => undefinedFilter.filtered.some((p) => p.structure_type !== "Stock"),
    [undefinedFilter.filtered],
  );
  const equityHasOptions = useMemo(
    () => equityFilter.filtered.some((p) => p.structure_type !== "Stock"),
    [equityFilter.filtered],
  );

  const definedColEntries = useMemo(() => positionColumnEntries(definedHasOptions, true), [definedHasOptions]);
  const undefinedColEntries = useMemo(() => positionColumnEntries(undefinedHasOptions, true), [undefinedHasOptions]);
  const equityColEntries = useMemo(() => positionColumnEntries(equityHasOptions, false), [equityHasOptions]);

  if (!portfolio) {
    return (
      <div className="section">
        <div className="section-header">
          <h2 className="section-title">
            <Circle size={14} />
            Portfolio
            <InfoTooltip text={SECTION_TOOLTIPS["Defined Risk Positions"]} />
          </h2>
          <span className="pill neutral">LOADING</span>
        </div>
        <div className="section-body">
          <div className="alert-item">Waiting for portfolio data...</div>
        </div>
      </div>
    );
  }

  return (
    <>
      {definedPositions.length > 0 && (
        <div className="section">
          <div className="section-header">
            <h2 className="section-title">
              <CheckCircle2 size={14} />
              Defined Risk Positions
              <InfoTooltip text={SECTION_TOOLTIPS["Defined Risk Positions"]} />
            </h2>
            <div className="section-header-actions">
              <ColumnsToggle<PositionToggleableColumnKey>
                columns={definedColEntries}
                visible={definedCols.visible}
                onToggle={definedCols.toggle}
                onReset={definedCols.reset}
              />
              <TableSearch
                query={definedFilter.query}
                setQuery={definedFilter.setQuery}
                placeholder="Filter positions..."
                resultCount={definedFilter.filtered.length}
                totalCount={definedPositions.length}
              />
              <span className="pill defined">{definedPositions.length} POSITIONS</span>
            </div>
          </div>
          <div className="section-body">
            <PositionTable positions={definedFilter.filtered} showUnderlying={true} prices={prices} portfolio={portfolio} tableId="positions-defined" columnVisibility={definedCols.visible} />
          </div>
        </div>
      )}

      {undefinedPositions.length > 0 && (
        <div className="section">
          <div className="section-header">
            <h2 className="section-title">
              <TriangleAlert size={14} />
              Undefined Risk Positions
              <InfoTooltip text={SECTION_TOOLTIPS["Undefined Risk Positions"]} />
            </h2>
            <div className="section-header-actions">
              <ColumnsToggle<PositionToggleableColumnKey>
                columns={undefinedColEntries}
                visible={undefinedCols.visible}
                onToggle={undefinedCols.toggle}
                onReset={undefinedCols.reset}
              />
              <TableSearch
                query={undefinedFilter.query}
                setQuery={undefinedFilter.setQuery}
                placeholder="Filter positions..."
                resultCount={undefinedFilter.filtered.length}
                totalCount={undefinedPositions.length}
              />
              <span className="pill undefined">{undefinedPositions.length} POSITIONS</span>
            </div>
          </div>
          <div className="section-body">
            <PositionTable positions={undefinedFilter.filtered} showUnderlying={true} prices={prices} portfolio={portfolio} tableId="positions-undefined" columnVisibility={undefinedCols.visible} />
          </div>
        </div>
      )}

      {equityPositions.length > 0 && (
        <div className="section">
          <div className="section-header">
            <h2 className="section-title">
              <Circle size={14} />
              Equity Positions
              <InfoTooltip text={SECTION_TOOLTIPS["Equity Positions"]} />
            </h2>
            <div className="section-header-actions">
              <ColumnsToggle<PositionToggleableColumnKey>
                columns={equityColEntries}
                visible={equityCols.visible}
                onToggle={equityCols.toggle}
                onReset={equityCols.reset}
              />
              <TableSearch
                query={equityFilter.query}
                setQuery={equityFilter.setQuery}
                placeholder="Filter positions..."
                resultCount={equityFilter.filtered.length}
                totalCount={equityPositions.length}
              />
              <span className="pill neutral">{equityPositions.length} POSITIONS</span>
            </div>
          </div>
          <div className="section-body">
            <PositionTable positions={equityFilter.filtered} showExpiry={false} prices={prices} portfolio={portfolio} tableId="positions-equity" columnVisibility={equityCols.visible} />
          </div>
        </div>
      )}

      <div className="section">
        <div className="report-meta">
          Last Sync: {new Date(portfolio.last_sync).toLocaleString()} • Source: IB Gateway
        </div>
      </div>
    </>
  );
}
