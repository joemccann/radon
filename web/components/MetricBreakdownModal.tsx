"use client";

import type { ReactNode } from "react";
import { Inbox } from "lucide-react";
import Modal from "./Modal";
import SectionEmptyState from "./SectionEmptyState";
import SortTh from "./SortTh";
import { useSort } from "@/lib/useSort";

/**
 * MetricBreakdownModal — shared primitive for click-to-explain metric modals.
 *
 * Composes <Modal> with the eb-* scaffold (eb-total / eb-formula / eb-table)
 * that PnlBreakdownModal, ExposureBreakdownModal, and AccountMetricModal each
 * grew independently. Owns:
 *   - the total value header (eb-total) with positive/negative/neutral toning
 *   - the formula proof block (eb-formula)
 *   - the per-row table (eb-table) with abs-magnitude row sort + per-cell tone
 *   - the empty fallback, routed through <SectionEmptyState>
 *   - mobile horizontal scroll via .table-wrap
 *
 * Two table modes:
 *   - declarative: pass `columns` + `rows`; rows are sorted by |sortValue| desc
 *     and rendered with per-cell tone. Optional `footer` renders a trailing row.
 *   - custom: pass `tableHead` + `tableBody` for surfaces that need expandable
 *     rows or bespoke cells (ExposureBreakdownModal).
 * Value-only mode: omit all table props (AccountMetricModal).
 */

export type MetricTone = "positive" | "negative" | "neutral";

export type MetricBreakdownCell = {
  content: ReactNode;
  className?: string;
  tone?: MetricTone;
  colSpan?: number;
};

export type MetricBreakdownColumn = {
  header: ReactNode;
  className?: string;
};

export type MetricBreakdownRow = {
  id: string | number;
  sortValue: number;
  values?: Array<string | number | null>;
  cells: MetricBreakdownCell[];
};

type MetricSortKey = string;

function metricExtract(row: MetricBreakdownRow, key: MetricSortKey): string | number | null {
  if (key === "magnitude") return Math.abs(row.sortValue);
  const i = Number(key);
  if (row.values && i in row.values) return row.values[i] ?? null;
  const content = row.cells[i]?.content;
  if (typeof content === "string" || typeof content === "number") return content;
  return null;
}

function toneClass(tone?: MetricTone): string {
  return tone ? ` ${tone}` : "";
}

function Cell({ cell }: { cell: MetricBreakdownCell }) {
  return (
    <td className={`${cell.className ?? ""}${toneClass(cell.tone)}`.trim()} colSpan={cell.colSpan}>
      {cell.content}
    </td>
  );
}

type Props = {
  open: boolean;
  title: string;
  className?: string;
  onClose: () => void;

  /** Formatted total value string (caller owns money formatting). */
  value: string;
  /** Toning for the total value. Omit for no tone class. */
  valueTone?: MetricTone;
  /** Optional detail line rendered under the total (eb-total-detail). */
  valueDetail?: ReactNode;

  /** Formula proof rendered inside eb-formula > code. */
  formula: ReactNode;

  /** Content rendered between the total and the formula (e.g. leverage block). */
  beforeFormula?: ReactNode;

  /** Declarative table columns. */
  columns?: MetricBreakdownColumn[];
  /** Declarative table rows (sorted by |sortValue| desc). */
  rows?: MetricBreakdownRow[];
  /** Optional trailing row (e.g. TOTAL) rendered after sorted rows. */
  footer?: MetricBreakdownCell[];

  /** Custom table head — overrides `columns` rendering. */
  tableHead?: ReactNode;
  /** Custom table body — overrides `rows` rendering. */
  tableBody?: ReactNode;
  /** True when the custom table has at least one row. */
  hasRows?: boolean;

  /** Empty-state copy routed through SectionEmptyState. */
  emptyMessage?: string;
};

export default function MetricBreakdownModal({
  open,
  title,
  className = "",
  onClose,
  value,
  valueTone,
  valueDetail,
  formula,
  beforeFormula,
  columns,
  rows,
  footer,
  tableHead,
  tableBody,
  hasRows,
  emptyMessage = "No data available.",
}: Props) {
  const { sorted: sortedRows, sort, toggle } = useSort(rows ?? [], metricExtract, "magnitude", "desc");

  if (!open) return null;

  const isCustomTable = tableHead != null || tableBody != null;
  const isValueOnly = !isCustomTable && columns == null;
  const showTable = isCustomTable ? Boolean(hasRows) : sortedRows.length > 0;

  return (
    <Modal open onClose={onClose} title={title} className={className.trim()}>
      <div className="eb-total">
        <span className={`eb-total-value${toneClass(valueTone)}`}>{value}</span>
        {valueDetail != null && <span className="eb-total-detail">{valueDetail}</span>}
      </div>

      {beforeFormula}

      <div className="eb-formula">
        <code>{formula}</code>
      </div>

      {!isValueOnly &&
        (showTable ? (
          <div className="table-wrap">
            <table className="eb-table">
              {tableHead ?? (
                <thead>
                  <tr>
                    {columns?.map((col, i) => (
                      <SortTh
                        key={i}
                        label={col.header}
                        sortKey={String(i)}
                        className={col.className}
                        activeKey={sort.key}
                        direction={sort.direction}
                        onToggle={toggle}
                      />
                    ))}
                  </tr>
                </thead>
              )}
              {tableBody ?? (
                <tbody>
                  {sortedRows.map((row) => (
                    <tr key={row.id} className="eb-row">
                      {row.cells.map((cell, i) => (
                        <Cell key={i} cell={cell} />
                      ))}
                    </tr>
                  ))}
                  {footer && (
                    <tr className="pb-total-row">
                      {footer.map((cell, i) => (
                        <Cell key={i} cell={cell} />
                      ))}
                    </tr>
                  )}
                </tbody>
              )}
            </table>
          </div>
        ) : (
          <div className="eb-empty">
            <SectionEmptyState icon={Inbox} headline={emptyMessage} testId="metric-breakdown-empty" />
          </div>
        ))}
    </Modal>
  );
}
