/**
 * @vitest-environment jsdom
 *
 * Cash-flows panel: "Synced Xh ago" lozenge.
 *
 * Background — IBKR Flex's CashTransaction section publishes once per
 * day with a ~1-day settlement lag (see feedback_flex_cash_transaction_lag.md).
 * A withdrawal initiated on day N appears in Flex on the morning of day
 * N+1. The daemon syncs once per ET trading day at 17:00 ET.
 *
 * Production bug we keep getting bitten by: operator initiates a
 * withdrawal, opens /orders, sees nothing reflecting it, files a bug.
 * Root cause is the upstream lag, not the radon code path — but the
 * panel gives the operator no way to tell. This lozenge surfaces the
 * last successful sync timestamp + a one-liner about the daily cadence
 * so the situation is self-explanatory.
 *
 * Contract:
 *   - render the lozenge when `data.last_synced_at` is a valid ISO ts
 *   - text contains a relative-time string ("Just now" / "Xm ago" / "Xh ago" / "Xd ago")
 *   - tooltip / aria-label explains the T+1 settlement lag
 *   - hidden when `last_synced_at` is null AND no rows (nothing to sync yet)
 *   - no em dashes in user-facing copy (CLAUDE.md mandatory rule)
 */
import { describe, it, expect, afterEach, vi, beforeEach } from "vitest";
import { render, cleanup, fireEvent, screen, within } from "@testing-library/react";
import CashFlowsSection from "../components/CashFlowsSection";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

// Stub the hook so we can drive the component from props-equivalent data.
vi.mock("../lib/useCashFlows", async () => {
  const actual = await vi.importActual<typeof import("../lib/useCashFlows")>(
    "../lib/useCashFlows",
  );
  return {
    ...actual,
    useCashFlows: vi.fn(),
  };
});

import { useCashFlows } from "../lib/useCashFlows";

const useCashFlowsMock = useCashFlows as unknown as ReturnType<typeof vi.fn>;

beforeEach(() => {
  useCashFlowsMock.mockReset();
});

function expand(): void {
  // Section is collapsed by default; click the toggle so the body renders.
  const toggle = screen.getByTestId("cash-flows-toggle");
  toggle.click();
}

describe("CashFlowsSection sync lozenge", () => {
  it("renders 'Synced Xh ago' when last_synced_at is recent", () => {
    // 3h45m ago — rounds to "3h ago"
    const synced = new Date(Date.now() - (3 * 3600 + 45 * 60) * 1000).toISOString();
    useCashFlowsMock.mockReturnValue({
      data: {
        rows: [
          {
            id: "39803040384",
            date: "2026-05-08",
            type: "Withdrawal",
            amount: -72_000,
            currency: "USD",
            description: "DISBURSEMENT INITIATED",
            raw_type: "Deposits/Withdrawals",
            synced_at: synced,
          },
        ],
        count: 1,
        from_date: "2026-02-20",
        summary: { deposits: 0, withdrawals: -72_000, dividends: 0, net: -72_000 },
        last_synced_at: synced,
      },
      loading: false,
      error: null,
      refresh: () => {},
    });

    render(<CashFlowsSection />);
    const lozenge = screen.getByTestId("cash-flows-sync-lozenge");
    expect(lozenge.textContent).toMatch(/3h ago/);
  });

  it("renders 'Just now' when last_synced_at is < 1 minute old", () => {
    const synced = new Date(Date.now() - 20_000).toISOString();
    useCashFlowsMock.mockReturnValue({
      data: {
        rows: [
          {
            id: "x",
            date: "2026-05-20",
            type: "Dividend",
            amount: 1,
            currency: "USD",
            description: null,
            raw_type: null,
            synced_at: synced,
          },
        ],
        count: 1,
        from_date: "2026-02-20",
        summary: { deposits: 0, withdrawals: 0, dividends: 1, net: 1 },
        last_synced_at: synced,
      },
      loading: false,
      error: null,
      refresh: () => {},
    });

    render(<CashFlowsSection />);
    const lozenge = screen.getByTestId("cash-flows-sync-lozenge");
    expect(lozenge.textContent).toMatch(/Just now/);
  });

  it("explains the IBKR Flex T+1 settlement lag via title attribute", () => {
    const synced = new Date(Date.now() - 6 * 3600 * 1000).toISOString();
    useCashFlowsMock.mockReturnValue({
      data: {
        rows: [
          {
            id: "x",
            date: "2026-05-20",
            type: "Withdrawal",
            amount: -1,
            currency: "USD",
            description: null,
            raw_type: null,
            synced_at: synced,
          },
        ],
        count: 1,
        from_date: "2026-02-20",
        summary: { deposits: 0, withdrawals: -1, dividends: 0, net: -1 },
        last_synced_at: synced,
      },
      loading: false,
      error: null,
      refresh: () => {},
    });

    render(<CashFlowsSection />);
    const lozenge = screen.getByTestId("cash-flows-sync-lozenge");
    const title = lozenge.getAttribute("title") ?? "";
    expect(title.toLowerCase()).toContain("ibkr flex");
    // The operator-visible explanation must NOT use em dashes (CLAUDE.md rule).
    expect(title.includes("—")).toBe(false);
    // And must mention the daily / T+1 contract so the user can self-diagnose.
    expect(title.toLowerCase()).toMatch(/daily|t\+1|once per/);
  });

  it("hides the lozenge when last_synced_at is null", () => {
    useCashFlowsMock.mockReturnValue({
      data: {
        rows: [],
        count: 0,
        from_date: "2026-02-20",
        summary: { deposits: 0, withdrawals: 0, dividends: 0, net: 0 },
        last_synced_at: null,
      },
      loading: false,
      error: null,
      refresh: () => {},
    });

    render(<CashFlowsSection />);
    expect(screen.queryByTestId("cash-flows-sync-lozenge")).toBeNull();
  });

  it("renders 'Xd ago' when last_synced_at is older than 24 hours", () => {
    const synced = new Date(Date.now() - 50 * 3600 * 1000).toISOString();
    useCashFlowsMock.mockReturnValue({
      data: {
        rows: [
          {
            id: "x",
            date: "2026-05-15",
            type: "Dividend",
            amount: 1,
            currency: "USD",
            description: null,
            raw_type: null,
            synced_at: synced,
          },
        ],
        count: 1,
        from_date: "2026-02-20",
        summary: { deposits: 0, withdrawals: 0, dividends: 1, net: 1 },
        last_synced_at: synced,
      },
      loading: false,
      error: null,
      refresh: () => {},
    });

    render(<CashFlowsSection />);
    const lozenge = screen.getByTestId("cash-flows-sync-lozenge");
    expect(lozenge.textContent).toMatch(/2d ago/);
  });

  it("surfaces Flex throttle state with warn tone + retry hint", () => {
    const synced = new Date(Date.now() - 24 * 3600 * 1000).toISOString();
    const nextAttempt = new Date(Date.now() + 22 * 3600 * 1000).toISOString();
    useCashFlowsMock.mockReturnValue({
      data: {
        rows: [
          {
            id: "x",
            date: "2026-05-08",
            type: "Withdrawal",
            amount: -72_000,
            currency: "USD",
            description: null,
            raw_type: null,
            synced_at: synced,
          },
        ],
        count: 1,
        from_date: "2026-02-20",
        summary: { deposits: 0, withdrawals: -72_000, dividends: 0, net: -72_000 },
        last_synced_at: synced,
        sync_status: {
          state: "error",
          last_attempt_at: new Date(Date.now() - 5 * 60_000).toISOString(),
          next_attempt_at: nextAttempt,
          error_summary: "Flex throttled by IBKR",
          is_throttled: true,
        },
      },
      loading: false,
      error: null,
      refresh: () => {},
    });

    render(<CashFlowsSection />);
    const lozenge = screen.getByTestId("cash-flows-sync-lozenge");
    expect(lozenge.textContent).toMatch(/Flex throttled/i);
    expect(lozenge.textContent).toMatch(/retry/i);
    // Wall-clock ET retry hint when more than 6h out.
    expect(lozenge.textContent).toMatch(/ET/);
    // Warn-tone classname so the operator's eye gets pulled to it.
    expect(lozenge.getAttribute("data-state")).toBe("warn");
    expect(lozenge.className).toContain("cash-flows-sync-lozenge--warn");
    // Tooltip explains the throttle pattern, not the generic T+1 lag.
    const title = lozenge.getAttribute("title") ?? "";
    expect(title.toLowerCase()).toMatch(/throttle/);
    // No em dashes in the user-visible copy (CLAUDE.md rule 6).
    expect(lozenge.textContent?.includes("—")).toBe(false);
    expect(title.includes("—")).toBe(false);
  });

  it("renders fault tone for non-throttle errors without the throttle copy", () => {
    const synced = new Date(Date.now() - 4 * 3600 * 1000).toISOString();
    useCashFlowsMock.mockReturnValue({
      data: {
        rows: [
          {
            id: "x",
            date: "2026-05-08",
            type: "Withdrawal",
            amount: -1,
            currency: "USD",
            description: null,
            raw_type: null,
            synced_at: synced,
          },
        ],
        count: 1,
        from_date: "2026-02-20",
        summary: { deposits: 0, withdrawals: -1, dividends: 0, net: -1 },
        last_synced_at: synced,
        sync_status: {
          state: "error",
          last_attempt_at: new Date(Date.now() - 60_000).toISOString(),
          next_attempt_at: new Date(Date.now() + 4 * 60_000).toISOString(),
          error_summary: "cash_flow_sync timed out after 180s",
          is_throttled: false,
        },
      },
      loading: false,
      error: null,
      refresh: () => {},
    });

    render(<CashFlowsSection />);
    const lozenge = screen.getByTestId("cash-flows-sync-lozenge");
    expect(lozenge.textContent).toMatch(/timed out/i);
    expect(lozenge.textContent).not.toMatch(/throttled/i);
    expect(lozenge.getAttribute("data-state")).toBe("fault");
    expect(lozenge.className).toContain("cash-flows-sync-lozenge--fault");
  });

  it("falls back to the calm ok tone when sync_status reports ok", () => {
    const synced = new Date(Date.now() - 30 * 60 * 1000).toISOString();
    useCashFlowsMock.mockReturnValue({
      data: {
        rows: [
          {
            id: "x",
            date: "2026-05-20",
            type: "Dividend",
            amount: 1,
            currency: "USD",
            description: null,
            raw_type: null,
            synced_at: synced,
          },
        ],
        count: 1,
        from_date: "2026-02-20",
        summary: { deposits: 0, withdrawals: 0, dividends: 1, net: 1 },
        last_synced_at: synced,
        sync_status: {
          state: "ok",
          last_attempt_at: synced,
          next_attempt_at: null,
          error_summary: null,
          is_throttled: false,
        },
      },
      loading: false,
      error: null,
      refresh: () => {},
    });

    render(<CashFlowsSection />);
    const lozenge = screen.getByTestId("cash-flows-sync-lozenge");
    expect(lozenge.textContent).toMatch(/30m ago/);
    expect(lozenge.textContent).not.toMatch(/throttled|failed|retry/i);
    expect(lozenge.getAttribute("data-state")).toBe("ok");
  });

  it("reorders cash-flow rows when Date is clicked", () => {
    useCashFlowsMock.mockReturnValue({
      data: {
        rows: [
          { id: "1", date: "2026-05-08", type: "Withdrawal", amount: -100, currency: "USD", description: "OUT", raw_type: "W", synced_at: "2026-05-08" },
          { id: "2", date: "2026-05-01", type: "Deposit", amount: 50, currency: "USD", description: "IN", raw_type: "D", synced_at: "2026-05-01" },
          { id: "3", date: "2026-05-04", type: "Dividend", amount: 5, currency: "USD", description: "DIV", raw_type: "Div", synced_at: "2026-05-04" },
        ],
        count: 3,
        from_date: "2026-02-20",
        summary: { deposits: 50, withdrawals: -100, dividends: 5, net: -45 },
        last_synced_at: new Date().toISOString(),
      },
      loading: false,
      error: null,
      refresh: () => {},
    });
    render(<CashFlowsSection />);
    fireEvent.click(screen.getByTestId("cash-flows-toggle"));
    const firstDate = () => within(screen.getByRole("table")).getAllByRole("row")[1].textContent ?? "";
    expect(firstDate()).toContain("05/08");
    fireEvent.click(screen.getByRole("columnheader", { name: /date/i }));
    expect(firstDate()).toContain("05/01");
    expect(screen.getByRole("columnheader", { name: /date/i }).getAttribute("aria-sort")).toBe("ascending");
  });

  it("does not append retry tomorrow on a Flex lockout with Monday next_attempt", () => {
    vi.useFakeTimers();
    // Sunday 2026-08-23 22:45 ET
    vi.setSystemTime(new Date("2026-08-24T02:45:00.000Z"));
    const lastSynced = new Date("2026-08-17T02:45:00.000Z").toISOString();
    useCashFlowsMock.mockReturnValue({
      data: {
        rows: [
          {
            id: "x",
            date: "2026-08-16",
            type: "Withdrawal",
            amount: -1,
            currency: "USD",
            description: null,
            raw_type: null,
            synced_at: lastSynced,
          },
        ],
        count: 1,
        from_date: "2026-05-25",
        summary: { deposits: 0, withdrawals: -1, dividends: 0, net: -1 },
        last_synced_at: lastSynced,
        sync_status: {
          state: "error",
          last_attempt_at: "2026-08-21T13:58:28.298780Z",
          next_attempt_at: "2026-08-24T12:00:00+00:00",
          error_summary: "Flex lockout. Do not retry. Ingest with --from-file",
          is_throttled: false,
        },
      },
      loading: false,
      error: null,
      refresh: () => {},
    });

    render(<CashFlowsSection />);
    const lozenge = screen.getByTestId("cash-flows-sync-lozenge");
    expect(lozenge.textContent).toMatch(/Flex lockout/i);
    expect(lozenge.textContent).toMatch(/Do not retry/i);
    expect(lozenge.textContent).not.toMatch(/retry.*tomorrow/i);
    expect(lozenge.textContent?.includes("—")).toBe(false);
  });
});
