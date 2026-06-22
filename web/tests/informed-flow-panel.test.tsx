/**
 * @vitest-environment jsdom
 *
 * F4 — congress + insider informed-flow surface UI guards.
 *
 * Behaviours pinned:
 *  - informedFlowBadge() summarises congress + insider activity into a compact
 *    label + bias bucket (buy-skew / sell-skew / mixed / none)
 *  - InformedFlowPanel renders congress + insider rows from mocked data
 *  - Empty payload renders the empty state, not an error
 *  - HTTP failure surfaces the error message, not a silent blank
 *  - Brand tokens only — no raw hex in the rendered markup
 */
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, waitFor, cleanup } from "@testing-library/react";

import { informedFlowBadge } from "@/lib/informedFlowBadge";
import { InformedFlowPanel } from "@/components/flow-analysis/InformedFlowPanel";

afterEach(() => {
  cleanup();
});

describe("informedFlowBadge", () => {
  it("returns none when there is no activity", () => {
    const badge = informedFlowBadge({ congress_trades: [], insider_trades: [], institutional_summary: null });
    expect(badge.bias).toBe("none");
    expect(badge.count).toBe(0);
  });

  it("counts congress + insider rows", () => {
    const badge = informedFlowBadge({
      congress_trades: [{ side: "BUY" }, { side: "BUY" }],
      insider_trades: [{ side: "SELL" }],
      institutional_summary: null,
    });
    expect(badge.count).toBe(3);
  });

  it("buckets a buy-skewed surface as buy-skew", () => {
    const badge = informedFlowBadge({
      congress_trades: [{ side: "BUY" }, { side: "BUY" }],
      insider_trades: [{ side: "BUY" }],
      institutional_summary: null,
    });
    expect(badge.bias).toBe("buy-skew");
  });

  it("buckets a sell-skewed surface as sell-skew", () => {
    const badge = informedFlowBadge({
      congress_trades: [{ side: "SELL" }],
      insider_trades: [{ side: "SELL" }, { side: "SELL" }],
      institutional_summary: null,
    });
    expect(badge.bias).toBe("sell-skew");
  });

  it("buckets a balanced surface as mixed", () => {
    const badge = informedFlowBadge({
      congress_trades: [{ side: "BUY" }],
      insider_trades: [{ side: "SELL" }],
      institutional_summary: null,
    });
    expect(badge.bias).toBe("mixed");
  });
});

describe("InformedFlowPanel", () => {
  const okPayload = {
    ticker: "AAPL",
    scan_time: "2026-06-21T12:00:00Z",
    congress_trades: [
      { ticker: "AAPL", person: "Nancy Pelosi", date: "2026-06-18", side: "BUY", amount: "$1,000,001 - $5,000,000", days_ago: 3 },
    ],
    insider_trades: [
      { ticker: "AAPL", person: "Timothy Cook", title: "CEO", date: "2026-06-19", side: "BUY", shares: 10000, days_ago: 2 },
    ],
    institutional_summary: { ticker: "AAPL", ownership_pct: 62, total_institutions: 4821, report_date: "2026-03-31" },
  };

  it("renders congress and insider rows from the loaded payload", async () => {
    global.fetch = vi.fn(async () => new Response(JSON.stringify(okPayload), { status: 200 })) as typeof fetch;
    render(<InformedFlowPanel ticker="AAPL" />);
    await waitFor(() => expect(screen.getByText("Nancy Pelosi")).toBeTruthy());
    expect(screen.getByText("Timothy Cook")).toBeTruthy();
  });

  it("renders the institutional ownership summary", async () => {
    global.fetch = vi.fn(async () => new Response(JSON.stringify(okPayload), { status: 200 })) as typeof fetch;
    render(<InformedFlowPanel ticker="AAPL" />);
    await waitFor(() => expect(screen.getByText(/4,?821/)).toBeTruthy());
  });

  it("renders the empty state when there is no informed flow", async () => {
    global.fetch = vi.fn(async () =>
      new Response(
        JSON.stringify({ ticker: "ZZZZ", missing: true, congress_trades: [], insider_trades: [], institutional_summary: null }),
        { status: 200 },
      ),
    ) as typeof fetch;
    render(<InformedFlowPanel ticker="ZZZZ" />);
    await waitFor(() => expect(screen.getByText(/no congress or insider activity/i)).toBeTruthy());
  });

  it("surfaces an error when the fetch fails", async () => {
    global.fetch = vi.fn(async () => new Response("boom", { status: 500 })) as typeof fetch;
    render(<InformedFlowPanel ticker="AAPL" />);
    await waitFor(() => expect(screen.getByText(/failed/i)).toBeTruthy());
  });

  it("renders no raw hex colors", async () => {
    global.fetch = vi.fn(async () => new Response(JSON.stringify(okPayload), { status: 200 })) as typeof fetch;
    const { container } = render(<InformedFlowPanel ticker="AAPL" />);
    await waitFor(() => expect(screen.getByText("Nancy Pelosi")).toBeTruthy());
    expect(container.innerHTML).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
  });
});
