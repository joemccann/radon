import type { CashFlowResponse, CashFlowRow, CashFlowType } from "@/lib/useCashFlows";
import { marketDateKey, shiftDateKey } from "./time";

type BuildCashFlowsOptions = {
  now?: Date;
  days?: number;
  types?: string;
};

type CashTemplate = {
  offsetDays: number;
  type: CashFlowType;
  amount: number;
  description: string;
  rawType: string;
};

const CASH_TEMPLATES: CashTemplate[] = [
  { offsetDays: 5, type: "Deposit", amount: 100_000, description: "Opening cash contribution", rawType: "Deposits/Withdrawals" },
  { offsetDays: 16, type: "Withdrawal", amount: -12_500, description: "Cash withdrawal", rawType: "Deposits/Withdrawals" },
  { offsetDays: 26, type: "Dividend", amount: 384.72, description: "Portfolio dividend", rawType: "Dividends" },
  { offsetDays: 52, type: "Fee", amount: -24.5, description: "Market data fees", rawType: "Fees" },
];

/** Mirrors the FastAPI days/types query contract using request-time sample rows. */
export function buildDemoCashFlows(options: BuildCashFlowsOptions = {}): CashFlowResponse {
  const now = options.now ?? new Date();
  const days = Number.isFinite(options.days) ? Math.max(1, Math.trunc(options.days ?? 90)) : 90;
  const today = marketDateKey(now);
  const fromDate = shiftDateKey(today, -days);
  const typeFilter = new Set(
    (options.types ?? "").split(",").map((value) => value.trim()).filter(Boolean),
  );
  const syncedAt = now.toISOString();
  const rows: CashFlowRow[] = CASH_TEMPLATES.map((template) => {
    const date = shiftDateKey(today, -template.offsetDays);
    return {
      id: `DEMO-${date}-${template.type.toUpperCase()}`,
      date,
      type: template.type,
      amount: template.amount,
      currency: "USD",
      description: template.description,
      raw_type: template.rawType,
      synced_at: syncedAt,
    };
  }).filter((row) => row.date >= fromDate && (typeFilter.size === 0 || typeFilter.has(row.type)));

  return {
    rows,
    count: rows.length,
    from_date: fromDate,
    summary: {
      deposits: rows.filter((row) => row.type === "Deposit").reduce((sum, row) => sum + row.amount, 0),
      withdrawals: rows.filter((row) => row.type === "Withdrawal").reduce((sum, row) => sum + row.amount, 0),
      dividends: rows.filter((row) => row.type === "Dividend").reduce((sum, row) => sum + row.amount, 0),
      net: rows.reduce((sum, row) => sum + row.amount, 0),
    },
    last_synced_at: rows.length > 0 ? syncedAt : null,
    sync_status: {
      state: "ok",
      last_attempt_at: syncedAt,
      next_attempt_at: null,
      error_summary: null,
      is_throttled: false,
    },
    db_error: null,
  };
}
