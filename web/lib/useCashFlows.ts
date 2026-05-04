"use client";

import { useCallback, useEffect, useRef, useState } from "react";

export type CashFlowType =
  | "Deposit"
  | "Withdrawal"
  | "Dividend"
  | "Interest"
  | "Fee"
  | "WithholdingTax"
  | "Other";

export interface CashFlowRow {
  id: string;
  date: string;
  type: CashFlowType;
  amount: number;
  currency: string;
  description: string | null;
  raw_type: string | null;
  synced_at: string;
}

export interface CashFlowSummary {
  deposits: number;
  withdrawals: number;
  dividends: number;
  net: number;
}

export interface CashFlowResponse {
  rows: CashFlowRow[];
  count: number;
  from_date: string;
  summary: CashFlowSummary | null;
  db_error?: string | null;
}

const POLL_INTERVAL_MS = 5 * 60_000;

interface UseCashFlowsReturn {
  data: CashFlowResponse | null;
  loading: boolean;
  error: string | null;
  refresh: () => void;
}

export function useCashFlows(days: number = 90, types: string = ""): UseCashFlowsReturn {
  const [data, setData] = useState<CashFlowResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const reqIdRef = useRef(0);

  const fetchOnce = useCallback(async () => {
    const myId = ++reqIdRef.current;
    try {
      const res = await fetch(
        `/api/cash-flows?days=${days}&types=${encodeURIComponent(types)}`,
        { cache: "no-store" },
      );
      if (!res.ok) throw new Error(`Failed to fetch cash flows (HTTP ${res.status})`);
      const json = (await res.json()) as CashFlowResponse;
      if (reqIdRef.current === myId) {
        setData(json);
        setError(null);
      }
    } catch (err) {
      if (reqIdRef.current === myId) {
        setError(err instanceof Error ? err.message : "Unknown error");
      }
    } finally {
      if (reqIdRef.current === myId) setLoading(false);
    }
  }, [days, types]);

  useEffect(() => {
    setLoading(true);
    fetchOnce();
    const id = setInterval(fetchOnce, POLL_INTERVAL_MS);
    return () => clearInterval(id);
  }, [fetchOnce]);

  return { data, loading, error, refresh: fetchOnce };
}
