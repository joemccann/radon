/**
 * @vitest-environment jsdom
 *
 * CompanyTab KEY STATISTICS: Shortable Shares, Borrow Fee, Public Float cells.
 *
 * Shortable Shares + Borrow Fee come from useShortAvailability (Next proxy ->
 * FastAPI /short-availability/{ticker}); Public Float comes from the info
 * payload's short_float (UW /api/shorts/{ticker}/interest-float/v2).
 * Gates: shortable cells render for stocks AND funds (!isIndex); Public Float
 * is equities-only (!hideEquityFundamentals); indexes get none of the three
 * and never fire the short-availability probe.
 */
import { render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function makeInfoPayload(overrides: Record<string, unknown> = {}) {
  const { uw_info, ...rest } = overrides;
  return {
    stock_state: {},
    profile: {},
    stats: {},
    short_float: { total_float: 850_000_000 },
    ...rest,
    uw_info: {
      issue_type: "Common Stock",
      full_name: "Apple Inc",
      sector: "Technology",
      marketcap: "3000000000000",
      ...((uw_info as Record<string, unknown>) ?? {}),
    },
  };
}

function makeShortPayload(overrides: Record<string, unknown> = {}) {
  return {
    ticker: "AAPL",
    shortable: true,
    difficulty: 3.0,
    shortable_shares: 5_400_000,
    fee_rate: 0.75,
    rebate_rate: 3.1,
    source: "ib",
    as_of: "2026-07-20T14:00:00Z",
    missing: false,
    ...overrides,
  };
}

function installFetch(info: unknown, short: unknown) {
  const spy = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/api/ticker/info")) return jsonResponse(info);
    if (url.includes("/api/short-availability/")) return jsonResponse(short);
    return jsonResponse({});
  });
  global.fetch = spy as unknown as typeof fetch;
  return spy;
}

function statValue(container: HTMLElement, label: string): string | null {
  for (const cell of Array.from(container.querySelectorAll(".pos-stat"))) {
    if (cell.querySelector(".pos-stat-label")?.textContent === label) {
      return cell.querySelector(".pos-stat-value")?.textContent ?? null;
    }
  }
  return null;
}

async function renderCompanyTab(info: unknown, short: unknown, ticker = "AAPL") {
  const spy = installFetch(info, short);
  const { default: CompanyTab } = await import(
    "@/components/ticker-detail/CompanyTab"
  );
  const view = render(
    <CompanyTab ticker={ticker} active priceData={null} fundamentals={null} />,
  );
  await waitFor(() =>
    expect(view.container.querySelector(".company-stats-grid")).not.toBeNull(),
  );
  return { spy, container: view.container };
}

describe("CompanyTab short stats cells", () => {
  const originalFetch = global.fetch;

  beforeEach(() => {
    vi.resetModules();
  });

  afterEach(() => {
    global.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("renders Shortable Shares from shortable_shares (5.4M)", async () => {
    const { container } = await renderCompanyTab(
      makeInfoPayload(),
      makeShortPayload(),
    );
    await waitFor(() =>
      expect(statValue(container, "Shortable Shares")).toBe("5.4M"),
    );
  });

  it("renders Borrow Fee as a two-decimal percent (0.75%)", async () => {
    const { container } = await renderCompanyTab(
      makeInfoPayload(),
      makeShortPayload(),
    );
    await waitFor(() =>
      expect(statValue(container, "Borrow Fee")).toBe("0.75%"),
    );
  });

  it("renders --- for Borrow Fee when fee_rate is null (IB-only payload)", async () => {
    const { container } = await renderCompanyTab(
      makeInfoPayload(),
      makeShortPayload({ fee_rate: null, rebate_rate: null, source: "ib" }),
    );
    await waitFor(() =>
      expect(statValue(container, "Shortable Shares")).toBe("5.4M"),
    );
    expect(statValue(container, "Borrow Fee")).toBe("---");
    expect(container.querySelector(".tab-error")).toBeNull();
  });

  it("renders --- for both short cells on a missing:true payload with no error UI", async () => {
    const { container } = await renderCompanyTab(
      makeInfoPayload(),
      makeShortPayload({
        shortable: null,
        difficulty: null,
        shortable_shares: null,
        fee_rate: null,
        rebate_rate: null,
        source: "none",
        missing: true,
      }),
    );
    expect(statValue(container, "Shortable Shares")).toBe("---");
    expect(statValue(container, "Borrow Fee")).toBe("---");
    expect(container.querySelector(".tab-error")).toBeNull();
  });

  it("renders Public Float from short_float.total_float (850.0M)", async () => {
    const { container } = await renderCompanyTab(
      makeInfoPayload({ short_float: { total_float: 850_000_000 } }),
      makeShortPayload(),
    );
    expect(statValue(container, "Public Float")).toBe("850.0M");
  });

  it("renders billion-scale floats with a B tier (AAPL 14.7B, not 14687.4M)", async () => {
    const { container } = await renderCompanyTab(
      makeInfoPayload({ short_float: { total_float: 14_687_356_000 } }),
      makeShortPayload(),
    );
    expect(statValue(container, "Public Float")).toBe("14.7B");
  });

  it("renders --- for Public Float when short_float is empty", async () => {
    const { container } = await renderCompanyTab(
      makeInfoPayload({ short_float: {} }),
      makeShortPayload(),
    );
    expect(statValue(container, "Public Float")).toBe("---");
  });

  it("ETF: keeps Shortable Shares + Borrow Fee, drops Public Float", async () => {
    const { container } = await renderCompanyTab(
      makeInfoPayload({ uw_info: { issue_type: "ETF" } }),
      makeShortPayload({ ticker: "BNO" }),
      "BNO",
    );
    await waitFor(() =>
      expect(statValue(container, "Shortable Shares")).toBe("5.4M"),
    );
    expect(statValue(container, "Borrow Fee")).toBe("0.75%");
    expect(statValue(container, "Public Float")).toBeNull();
  });

  it("INDEX: renders none of the three cells and never probes short availability", async () => {
    const { spy, container } = await renderCompanyTab(
      makeInfoPayload({ uw_info: { issue_type: "INDEX" } }),
      makeShortPayload({ ticker: "SPX" }),
      "SPX",
    );
    expect(statValue(container, "Shortable Shares")).toBeNull();
    expect(statValue(container, "Borrow Fee")).toBeNull();
    expect(statValue(container, "Public Float")).toBeNull();
    const shortCalls = spy.mock.calls.filter(([input]) =>
      String(input).includes("/api/short-availability/"),
    );
    expect(shortCalls).toHaveLength(0);
  });

  it("probes /api/short-availability/AAPL with cache no-store + abort signal", async () => {
    const { spy, container } = await renderCompanyTab(
      makeInfoPayload(),
      makeShortPayload(),
    );
    await waitFor(() =>
      expect(statValue(container, "Shortable Shares")).toBe("5.4M"),
    );
    expect(spy).toHaveBeenCalledWith(
      "/api/short-availability/AAPL",
      expect.objectContaining({
        cache: "no-store",
        signal: expect.any(AbortSignal),
      }),
    );
  });
});
