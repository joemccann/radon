"use client";

import { useMemo, useState } from "react";
import { useCashFlows, type CashFlowRow, type CashFlowType } from "@/lib/useCashFlows";

const TYPE_TONE: Record<CashFlowType, "positive" | "negative" | "neutral"> = {
  Deposit: "positive",
  Dividend: "positive",
  Interest: "positive",
  Withdrawal: "negative",
  Fee: "negative",
  WithholdingTax: "negative",
  Other: "neutral",
};

function fmtUsd(n: number): string {
  const sign = n >= 0 ? "+" : "-";
  const abs = Math.abs(n);
  return `${sign}$${abs.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function fmtUsdAbs(n: number): string {
  return `$${Math.abs(n).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function fmtDate(iso: string): string {
  if (!iso || iso.length < 10) return iso;
  const [y, m, d] = iso.split("-");
  return `${m}/${d}/${y.slice(2)}`;
}

const PAGE_SIZE = 15;

export default function CashFlowsSection() {
  const { data, loading, error } = useCashFlows(90);
  const [page, setPage] = useState(0);
  const [filter, setFilter] = useState<"all" | CashFlowType>("all");

  const rows = useMemo(() => {
    const all = data?.rows ?? [];
    if (filter === "all") return all;
    return all.filter((r) => r.type === filter);
  }, [data?.rows, filter]);

  const summary = data?.summary ?? null;
  const totalPages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
  const pageStart = page * PAGE_SIZE;
  const pageRows = rows.slice(pageStart, pageStart + PAGE_SIZE);

  return (
    <div className="section" data-testid="cash-flows-section">
      <div className="section-header">
        <h2 className="section-title">CASH FLOWS (90 DAYS)</h2>
        <div className="section-meta" style={{ display: "flex", gap: 16, alignItems: "center" }}>
          {summary && (
            <>
              <span style={{ fontSize: 11 }}>
                <span style={{ color: "var(--text-muted)" }}>DEPOSITS</span>{" "}
                <span style={{ color: "var(--positive)" }}>{fmtUsdAbs(summary.deposits)}</span>
              </span>
              <span style={{ fontSize: 11 }}>
                <span style={{ color: "var(--text-muted)" }}>WITHDRAWALS</span>{" "}
                <span style={{ color: "var(--negative)" }}>{fmtUsdAbs(summary.withdrawals)}</span>
              </span>
              <span style={{ fontSize: 11 }}>
                <span style={{ color: "var(--text-muted)" }}>NET</span>{" "}
                <span style={{ color: summary.net >= 0 ? "var(--positive)" : "var(--negative)" }}>
                  {fmtUsd(summary.net)}
                </span>
              </span>
            </>
          )}
          <select
            className="filter-select"
            value={filter}
            onChange={(e) => {
              setFilter(e.target.value as typeof filter);
              setPage(0);
            }}
          >
            <option value="all">ALL</option>
            <option value="Deposit">Deposits</option>
            <option value="Withdrawal">Withdrawals</option>
            <option value="Dividend">Dividends</option>
            <option value="Interest">Interest</option>
            <option value="Fee">Fees</option>
            <option value="WithholdingTax">Withholding Tax</option>
            <option value="Other">Other</option>
          </select>
          <span style={{ fontSize: 11 }}>{rows.length} TXNS</span>
        </div>
      </div>

      {error && (
        <div className="error-banner" style={{ marginTop: 8 }}>
          Failed to load cash flows: {error}
        </div>
      )}

      {loading && !data ? (
        <div style={{ padding: 24, textAlign: "center", color: "var(--text-muted)" }}>Loading…</div>
      ) : rows.length === 0 ? (
        <div style={{ padding: 24, textAlign: "center", color: "var(--text-muted)" }}>
          No cash transactions in the last 90 days.
        </div>
      ) : (
        <>
          <table className="data-table">
            <thead>
              <tr>
                <th style={{ width: 110 }}>DATE</th>
                <th style={{ width: 130 }}>TYPE</th>
                <th style={{ width: 160, textAlign: "right" }}>AMOUNT</th>
                <th>DESCRIPTION</th>
              </tr>
            </thead>
            <tbody>
              {pageRows.map((row: CashFlowRow) => (
                <tr key={row.id} data-testid={`cash-flow-row-${row.id}`}>
                  <td>{fmtDate(row.date)}</td>
                  <td>
                    <span className={`pill pill-${TYPE_TONE[row.type] ?? "neutral"}`}>{row.type}</span>
                  </td>
                  <td
                    style={{
                      textAlign: "right",
                      color: row.amount >= 0 ? "var(--positive)" : "var(--negative)",
                      fontVariantNumeric: "tabular-nums",
                    }}
                  >
                    {fmtUsd(row.amount)}
                  </td>
                  <td style={{ color: "var(--text-muted)", fontSize: 11 }}>{row.description ?? row.raw_type ?? ""}</td>
                </tr>
              ))}
            </tbody>
          </table>

          {totalPages > 1 && (
            <div style={{ display: "flex", justifyContent: "space-between", padding: 8, alignItems: "center" }}>
              <button className="btn-secondary" disabled={page === 0} onClick={() => setPage(page - 1)}>
                ← Prev
              </button>
              <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
                Page {page + 1} of {totalPages}
              </span>
              <button className="btn-secondary" disabled={page >= totalPages - 1} onClick={() => setPage(page + 1)}>
                Next →
              </button>
            </div>
          )}
        </>
      )}
    </div>
  );
}
