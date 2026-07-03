// Trial-expiry resolution for the demo webhook + operator EXTEND (Phase 2/6).
//
// The 2-trading-day window depends on the US market calendar (weekends +
// holidays + IBKR closures), which lives only in Python
// (scripts/utils/demo_trial.py). So the Next.js side delegates the computation
// to the FastAPI `/demo/trial-expiry` helper rather than re-implementing — and
// risking drift in — a holiday calendar in TypeScript.
//
// The transport is INJECTED so this is unit-testable without a live FastAPI.

export type TrialExpiry = {
  startedAt: string;
  expiresAt: string;
  tradingDays: number;
  active: boolean;
};

export type TrialExpiryFetcher = (
  startIsoEt: string,
  tradingDays: number,
) => Promise<{
  started_at: string;
  expires_at: string;
  trading_days: number;
  active: boolean;
}>;

export const DEFAULT_DEMO_TRADING_DAYS = 2;

// Default transport: POST the start timestamp to the FastAPI helper.
const radonFetcher: TrialExpiryFetcher = async (startIsoEt, tradingDays) => {
  const { radonFetch } = await import("@/lib/radonApi");
  return (await radonFetch("/demo/trial-expiry", {
    method: "POST",
    body: JSON.stringify({ start_iso_et: startIsoEt, trading_days: tradingDays }),
    timeout: 10_000,
  })) as {
    started_at: string;
    expires_at: string;
    trading_days: number;
    active: boolean;
  };
};

export async function computeTrialExpiry(
  startIsoEt: string,
  opts?: { tradingDays?: number; fetcher?: TrialExpiryFetcher },
): Promise<TrialExpiry> {
  const tradingDays = opts?.tradingDays ?? DEFAULT_DEMO_TRADING_DAYS;
  const fetcher = opts?.fetcher ?? radonFetcher;
  const res = await fetcher(startIsoEt, tradingDays);
  return {
    startedAt: res.started_at,
    expiresAt: res.expires_at,
    tradingDays: res.trading_days,
    active: res.active,
  };
}
