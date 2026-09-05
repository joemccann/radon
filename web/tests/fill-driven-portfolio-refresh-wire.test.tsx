/**
 * @vitest-environment jsdom
 *
 * T-459: fill-driven portfolio refresh tested AT THE WIRE.
 *
 * fill-driven-portfolio-refresh.test.tsx asserts a vi.fn() callback. The
 * production callback (WorkspaceShell.tsx) is a gate-closing useCallback
 * (`if (isDemoMode) return; portfolioSyncNow();`) and useFillToasts reads it
 * through a ref. This test renders the smallest REAL tree owning both the
 * callback wiring and the fetch (real usePortfolio + real useFillToasts,
 * WorkspaceShell's exact callback shape) and asserts the portfolio producer
 * request — POST /api/portfolio — fires when a new execId arrives.
 *
 * The gate flips between mount and the fill (demo -> live), so a stale-ref
 * mutation of useFillToasts (ref captured once, never updated) replays the
 * CLOSED gate and reds this test — the 2026-08-27 stale-Transmit shape.
 */
import React, { useCallback } from "react";
import { act, cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ExecutedOrder, OrderContract, OrdersData, PortfolioData } from "@/lib/types";
import { SEEN_STORAGE_KEY } from "@/lib/fillToasts";
import { useFillToasts } from "@/lib/useFillToasts";
import { usePortfolio } from "@/lib/usePortfolio";

function makeContract(): OrderContract {
  return { conId: 12345, symbol: "VIX", secType: "OPT", strike: 30, right: "C", expiry: "20261020" };
}

function makeFill(execId: string): ExecutedOrder {
  return {
    execId,
    symbol: "VIX",
    contract: makeContract(),
    side: "BOT",
    quantity: 2,
    avgPrice: 0.61,
    commission: null,
    realizedPNL: null,
    time: new Date().toISOString(),
    exchange: "SMART",
  };
}

function makeOrders(executed: ExecutedOrder[]): OrdersData {
  return {
    last_sync: new Date().toISOString(),
    open_orders: [],
    executed_orders: executed,
    open_count: 0,
    executed_count: executed.length,
  };
}

const BASELINE = [makeFill("a.1.01"), makeFill("a.2.01")];

const PORTFOLIO_JSON = { positions: [], bankroll: 1_000_000, last_sync: "2026-09-05T14:00:00Z" } as unknown as PortfolioData;

/**
 * WorkspaceShell's exact wiring, reduced to the tree that owns the callback
 * and the fetch: usePortfolio owns every /api/portfolio request, and the
 * onNewFills useCallback is the same demo-gated closure over syncNow.
 */
function FillRefreshHarness({ orders, isDemoMode }: { orders: OrdersData | null; isDemoMode: boolean }) {
  const { syncNow: portfolioSyncNow } = usePortfolio(false);
  const upsertToast = useCallback(() => "", []);
  const onNewFills = useCallback(() => {
    if (isDemoMode) return;
    portfolioSyncNow();
  }, [isDemoMode, portfolioSyncNow]);
  useFillToasts(orders, upsertToast, onNewFills);
  return null;
}

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  window.sessionStorage.removeItem(SEEN_STORAGE_KEY);
  fetchMock = vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    headers: new Headers(),
    json: async () => PORTFOLIO_JSON,
  });
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

const portfolioPosts = () =>
  fetchMock.mock.calls.filter(
    ([url, init]) => url === "/api/portfolio" && (init as RequestInit | undefined)?.method === "POST",
  );

describe("fill-driven portfolio refresh wire contract", () => {
  it("does not hit the portfolio producer for the baseline payload", async () => {
    render(<FillRefreshHarness orders={makeOrders(BASELINE)} isDemoMode={false} />);
    await act(async () => {});
    expect(portfolioPosts()).toHaveLength(0);
  });

  it("POSTs /api/portfolio when a new execId arrives after the gate opens", async () => {
    const { rerender } = render(
      <FillRefreshHarness orders={makeOrders(BASELINE)} isDemoMode={true} />,
    );
    await act(async () => {});
    expect(portfolioPosts()).toHaveLength(0);

    // Gate flips open (demo -> live) with no new fills: still nothing fired.
    rerender(<FillRefreshHarness orders={makeOrders(BASELINE)} isDemoMode={false} />);
    await act(async () => {});
    expect(portfolioPosts()).toHaveLength(0);

    // New execId lands: the CURRENT (open-gate) callback must run and the
    // producer sync must reach the wire.
    rerender(
      <FillRefreshHarness
        orders={makeOrders([...BASELINE, makeFill("b.9.01")])}
        isDemoMode={false}
      />,
    );
    await act(async () => {});

    const posts = portfolioPosts();
    expect(posts).toHaveLength(1);
    const [url, init] = posts[0] as [string, RequestInit];
    expect(url).toBe("/api/portfolio");
    expect(init.method).toBe("POST");
  });

  it("keeps the demo gate closed at the wire when a fill arrives in demo mode", async () => {
    const { rerender } = render(
      <FillRefreshHarness orders={makeOrders(BASELINE)} isDemoMode={true} />,
    );
    await act(async () => {});
    rerender(
      <FillRefreshHarness
        orders={makeOrders([...BASELINE, makeFill("c.7.01")])}
        isDemoMode={true}
      />,
    );
    await act(async () => {});
    expect(portfolioPosts()).toHaveLength(0);
  });
});
