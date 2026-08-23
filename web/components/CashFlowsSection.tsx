"use client";

import { useMemo, useState } from "react";
import { ChevronDown, TriangleAlert, Wallet } from "lucide-react";
import { useCashFlows, type CashFlowRow, type CashFlowType } from "@/lib/useCashFlows";
import SectionEmptyState from "@/components/SectionEmptyState";
import SortTh from "@/components/SortTh";
import { TableSkeleton } from "@/components/ui/Skeleton";
import { useSort } from "@/lib/useSort";

type CashSortKey = "date" | "type" | "amount" | "description";

function cashExtract(row: CashFlowRow, key: CashSortKey): string | number | null {
  switch (key) {
    case "date": return row.date;
    case "type": return row.type;
    case "amount": return row.amount;
    case "description": return row.description ?? row.raw_type ?? null;
    default: return null;
  }
}

const TYPE_TONE: Record<CashFlowType, "accum" | "distrib" | "neutral"> = {
  Deposit: "accum",
  Dividend: "accum",
  Interest: "accum",
  Withdrawal: "distrib",
  Fee: "distrib",
  WithholdingTax: "distrib",
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

// IBKR Flex publishes the CashTransaction section once per day with a
// ~1-day settlement lag — a withdrawal initiated on day N appears in
// Flex on the morning of day N+1, and the radon daemon syncs once per
// ET trading day at 17:00 ET. The lozenge surfaces the last successful
// sync so an operator who initiated a withdrawal today understands why
// it hasn't appeared yet. See feedback_flex_cash_transaction_lag.md.
const SYNC_LOZENGE_EXPLANATION =
  "IBKR Flex publishes cash transactions once per day with a ~1-day settlement lag. A withdrawal initiated today appears here after tomorrow morning's sync (T+1).";

const THROTTLE_LOZENGE_EXPLANATION =
  "IBKR Flex returned a throttle code (1001/1018/1019) on the last sync attempt. The daemon embargoes itself for 24h so retries don't extend the throttle window. New cash flows land on the next successful pull.";

function relativeFromNow(isoTimestamp: string, now: number = Date.now()): string | null {
  const parsed = Date.parse(isoTimestamp);
  if (Number.isNaN(parsed)) return null;
  const ageSeconds = Math.max(0, Math.floor((now - parsed) / 1000));
  if (ageSeconds < 60) return "Just now";
  const ageMinutes = Math.floor(ageSeconds / 60);
  if (ageMinutes < 60) return `${ageMinutes}m ago`;
  const ageHours = Math.floor(ageMinutes / 60);
  if (ageHours < 24) return `${ageHours}h ago`;
  const ageDays = Math.floor(ageHours / 24);
  return `${ageDays}d ago`;
}

/** Format a future ISO timestamp as a short retry hint:
 *    in 12m / in 4h / 17:00 ET tomorrow / 17:00 ET Mon
 *  Null when the timestamp is in the past or unparseable. */
function formatNextAttempt(isoTimestamp: string, now: number = Date.now()): string | null {
  const parsed = Date.parse(isoTimestamp);
  if (Number.isNaN(parsed)) return null;
  const deltaSeconds = Math.floor((parsed - now) / 1000);
  if (deltaSeconds <= 0) return "due now";
  if (deltaSeconds < 60 * 60) return `in ${Math.max(1, Math.floor(deltaSeconds / 60))}m`;
  if (deltaSeconds < 6 * 60 * 60) return `in ${Math.floor(deltaSeconds / 3600)}h`;
  // More than 6 hours out — show wall-clock ET time so the operator can
  // map it to their day rather than counting hours.
  const target = new Date(parsed);
  const sameEtDate =
    new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York" }).format(target) ===
    new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York" }).format(new Date(now));
  const time = new Intl.DateTimeFormat("en-US", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    timeZone: "America/New_York",
  }).format(target);
  if (sameEtDate) return `${time} ET today`;
  const ageDays = Math.floor(deltaSeconds / 86400);
  if (ageDays <= 1) return `${time} ET tomorrow`;
  const weekday = new Intl.DateTimeFormat("en-US", {
    weekday: "short",
    timeZone: "America/New_York",
  }).format(target);
  return `${time} ET ${weekday}`;
}

const PAGE_SIZE = 15;

export default function CashFlowsSection() {
  const { data, loading, error } = useCashFlows(90);
  const [page, setPage] = useState(0);
  const [filter, setFilter] = useState<"all" | CashFlowType>("all");
  const [expanded, setExpanded] = useState(false);

  const rows = useMemo(() => {
    const all = data?.rows ?? [];
    if (filter === "all") return all;
    return all.filter((r) => r.type === filter);
  }, [data?.rows, filter]);
  const { sorted, sort, toggle } = useSort(rows, cashExtract, "date", "desc");

  const summary = data?.summary ?? null;
  const lastSyncedRelative = data?.last_synced_at ? relativeFromNow(data.last_synced_at) : null;
  const syncStatus = data?.sync_status ?? null;
  // Throttle dominates the failure modes for cash-flow-sync; treat any
  // is_throttled flag as the canonical "Flex throttled" state. Other
  // error states fall through to a generic warn treatment.
  const isThrottled = syncStatus?.state === "error" && Boolean(syncStatus?.is_throttled);
  const isErrored = syncStatus?.state === "error" && !isThrottled;
  const isLockout = Boolean(syncStatus?.error_summary?.includes("Do not retry"));
  const retryHint = !isLockout && syncStatus?.next_attempt_at
    ? formatNextAttempt(syncStatus.next_attempt_at)
    : null;
  const lozengeTone: "ok" | "warn" | "fault" = isThrottled ? "warn" : isErrored ? "fault" : "ok";
  const lozengeLabel = (() => {
    // R-135: the failure branches run BEFORE the no-prior-sync bail-out. A
    // fresh environment (or a truncated cash_flows table) that hits a 1025
    // on its first pull has no `last_synced_at`, so keying the whole lozenge
    // off one showed an empty table with no error, no retry hint and no tone
    // for the full 7-day embargo.
    const syncedPrefix = lastSyncedRelative ? `Synced ${lastSyncedRelative}` : "Never synced";
    if (isThrottled) {
      return retryHint
        ? `${syncedPrefix} · Flex throttled, retry ${retryHint}`
        : `${syncedPrefix} · Flex throttled`;
    }
    if (isErrored) {
      const tag = syncStatus?.error_summary ?? "sync failed";
      return retryHint ? `${syncedPrefix} · ${tag}, retry ${retryHint}` : `${syncedPrefix} · ${tag}`;
    }
    if (!lastSyncedRelative) return null;
    return `Synced ${lastSyncedRelative}`;
  })();
  const lozengeTooltip = isThrottled
    ? THROTTLE_LOZENGE_EXPLANATION
    : isErrored
      ? syncStatus?.error_summary ?? SYNC_LOZENGE_EXPLANATION
      : SYNC_LOZENGE_EXPLANATION;
  const totalPages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
  const pageStart = page * PAGE_SIZE;
  const pageRows = sorted.slice(pageStart, pageStart + PAGE_SIZE);
  const stopToggle = (e: React.SyntheticEvent) => e.stopPropagation();

  return (
    <div className="section" id="orders-cash" data-testid="cash-flows-section">
      <div
        className="section-header cash-flows-header"
        role="button"
        tabIndex={0}
        aria-expanded={expanded}
        aria-controls="cash-flows-body"
        data-testid="cash-flows-toggle"
        onClick={() => setExpanded((v) => !v)}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            setExpanded((v) => !v);
          }
        }}
        style={{ cursor: "pointer", userSelect: "none" }}
      >
        <div className="section-title">
          <ChevronDown
            size={12}
            style={{
              transform: expanded ? "rotate(0deg)" : "rotate(-90deg)",
              transition: "transform 150ms ease",
            }}
          />
          CASH FLOWS (90 DAYS)
        </div>
        <div className="cash-flows-meta">
          {summary && (
            <>
              <span className="cash-flows-stat">
                <span className="cash-flows-stat-label">DEPOSITS</span>
                <span className="cash-flows-stat-value positive">{fmtUsdAbs(summary.deposits)}</span>
              </span>
              <span className="cash-flows-stat">
                <span className="cash-flows-stat-label">WITHDRAWALS</span>
                <span className="cash-flows-stat-value negative">{fmtUsdAbs(summary.withdrawals)}</span>
              </span>
              <span className="cash-flows-stat">
                <span className="cash-flows-stat-label">NET</span>
                <span
                  className={`cash-flows-stat-value ${summary.net >= 0 ? "positive" : "negative"}`}
                >
                  {fmtUsd(summary.net)}
                </span>
              </span>
            </>
          )}
          {lozengeLabel && (
            <span
              className={`cash-flows-sync-lozenge cash-flows-sync-lozenge--${lozengeTone}`}
              data-testid="cash-flows-sync-lozenge"
              data-state={lozengeTone}
              title={lozengeTooltip}
              onClick={stopToggle}
              onKeyDown={stopToggle}
            >
              {lozengeLabel}
            </span>
          )}
          <select
            className="filter-select"
            value={filter}
            onClick={stopToggle}
            onChange={(e) => {
              setFilter(e.target.value as typeof filter);
              setPage(0);
            }}
            onKeyDown={stopToggle}
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
          <span className="pill defined">{rows.length} TXNS</span>
        </div>
      </div>

      {expanded && (
        <div id="cash-flows-body">
          {error && (
            <div className="section-body">
              <SectionEmptyState
                icon={TriangleAlert}
                tone="danger"
                headline="Couldn't load cash flows"
                secondary={error}
                testId="cash-flows-error"
              />
            </div>
          )}

          {!error && loading && !data ? (
            <div className="section-body p-6">
              <TableSkeleton rows={4} columns={4} />
            </div>
          ) : !error && rows.length === 0 ? (
            <div className="section-body">
              <SectionEmptyState
                icon={Wallet}
                headline="No cash transactions in the last 90 days"
                secondary="Deposits, withdrawals, dividends, and fees appear here once IBKR settles them."
                testId="cash-flows-empty"
              />
            </div>
          ) : !error ? (
            <>
              <div className="section-body table-wrap">
                <table>
                  <thead>
                    <tr>
                      <SortTh<CashSortKey> label="Date" sortKey="date" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                      <SortTh<CashSortKey> label="Type" sortKey="type" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                      <SortTh<CashSortKey> label="Amount" sortKey="amount" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                      <SortTh<CashSortKey> label="Description" sortKey="description" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                    </tr>
                  </thead>
                  <tbody>
                    {pageRows.map((row: CashFlowRow) => (
                      <tr key={row.id} data-testid={`cash-flow-row-${row.id}`}>
                        <td>{fmtDate(row.date)}</td>
                        <td>
                          <span className={`pill ${TYPE_TONE[row.type] ?? "neutral"}`}>
                            {row.type}
                          </span>
                        </td>
                        <td className={`right ${row.amount >= 0 ? "positive" : "negative"}`}>
                          {fmtUsd(row.amount)}
                        </td>
                        <td className="cell-muted">{row.description ?? row.raw_type ?? ""}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              {totalPages > 1 && (
                <div className="cash-flows-pagination">
                  <button
                    className="btn-secondary"
                    disabled={page === 0}
                    onClick={() => setPage(page - 1)}
                  >
                    ← Prev
                  </button>
                  <span className="cell-muted">
                    Page {page + 1} of {totalPages}
                  </span>
                  <button
                    className="btn-secondary"
                    disabled={page >= totalPages - 1}
                    onClick={() => setPage(page + 1)}
                  >
                    Next →
                  </button>
                </div>
              )}
            </>
          ) : null}
        </div>
      )}
    </div>
  );
}
