/**
 * @vitest-environment jsdom
 *
 * F3 — catalyst feed UI guards.
 *
 * Behaviours pinned:
 *  - catalystBadge() days-to-catalyst label + urgency bucket are correct, and
 *    a negative (past) days_until NEVER labels "Today"
 *  - CatalystCard recomputes days_until from each row's date at render time —
 *    the stored integer is advisory only (the writer legitimately skips
 *    weekends/holidays, so the snapshot can be days old)
 *  - Past events never render; a fossil snapshot renders the empty state
 *  - Empty payload renders the empty state, not an error
 *  - HTTP failure surfaces the error message, not a silent blank
 *  - Brand tokens only — no raw hex in the rendered markup
 */
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, waitFor, cleanup } from "@testing-library/react";

import { catalystBadge } from "@/lib/catalystBadge";
import { CatalystCard } from "@/components/dashboard/CatalystCard";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

function freezeClock(instant: string) {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  vi.setSystemTime(new Date(instant));
}

function stubCatalystsFetch(payload: unknown, status = 200) {
  global.fetch = vi.fn(async () =>
    new Response(typeof payload === "string" ? payload : JSON.stringify(payload), { status }),
  ) as typeof fetch;
}

function catRow(date: string, daysUntil: number, overrides: Record<string, unknown> = {}) {
  return {
    ticker: null,
    type: "economic",
    title: `Event ${date}`,
    date,
    source: "economic",
    days_until: daysUntil,
    ...overrides,
  };
}

describe("catalystBadge", () => {
  it("labels today as Today", () => {
    expect(catalystBadge(0).label).toBe("Today");
  });

  it("never labels a past event Today", () => {
    expect(catalystBadge(-1).label).not.toBe("Today");
    expect(catalystBadge(-3).label).not.toBe("Today");
  });

  it("labels one day out as 1d", () => {
    expect(catalystBadge(1).label).toBe("1d");
  });

  it("labels multi-day as Nd", () => {
    expect(catalystBadge(5).label).toBe("5d");
  });

  it("buckets imminent (<=1d) as imminent", () => {
    expect(catalystBadge(0).urgency).toBe("imminent");
    expect(catalystBadge(1).urgency).toBe("imminent");
  });

  it("buckets near (<=7d) as near", () => {
    expect(catalystBadge(3).urgency).toBe("near");
    expect(catalystBadge(7).urgency).toBe("near");
  });

  it("buckets distant (>7d) as distant", () => {
    expect(catalystBadge(14).urgency).toBe("distant");
  });
});

describe("CatalystCard", () => {
  // Regular trading Monday, 12:00 ET.
  const MONDAY_REGULAR = "2026-07-06T16:00:00Z";
  // Sunday after the observed July-4 holiday, 15:00 ET — the writer last ran
  // Thursday, so every stored days_until is 3+ days stale.
  const SUNDAY_AFTER_JULY4 = "2026-07-05T19:00:00Z";

  it("renders rows with days_until recomputed from the date, not the stored value", async () => {
    freezeClock(MONDAY_REGULAR);
    stubCatalystsFetch({
      scan_time: "2026-07-06T10:30:00Z",
      count: 2,
      catalysts: [
        catRow("2026-07-06", 0, { ticker: "AAPL", type: "earnings", title: "APPLE INC", source: "earnings_premarket" }),
        // Stored days_until is a stale 0 — the card must show 2d, not Today.
        catRow("2026-07-08", 0, { title: "PCE index" }),
      ],
    });
    render(<CatalystCard />);
    await waitFor(() => expect(screen.getByText("AAPL")).toBeTruthy());
    expect(screen.getByText("PCE index")).toBeTruthy();
    expect(screen.getByText("Today")).toBeTruthy(); // AAPL, genuinely today
    expect(screen.getByText("2d")).toBeTruthy(); // PCE, recomputed
    expect(screen.getAllByText("Today")).toHaveLength(1);
  });

  it("renders the empty state on Sunday when the snapshot is last week's fossil", async () => {
    freezeClock(SUNDAY_AFTER_JULY4);
    stubCatalystsFetch({
      scan_time: "2026-07-02T23:07:38Z",
      count: 3,
      catalysts: [
        catRow("2026-07-02", 0, { ticker: "LNN", type: "earnings", title: "LINDSAY", source: "earnings_premarket" }),
        catRow("2026-07-02", 0, { title: "Factory orders" }),
        catRow("2026-07-02", 0, { title: "Initial jobless claims" }),
      ],
    });
    render(<CatalystCard />);
    await waitFor(() => expect(screen.getByText(/no upcoming catalysts/i)).toBeTruthy());
    expect(screen.queryByText("Today")).toBeNull();
    expect(screen.queryByText("LINDSAY")).toBeNull();
  });

  it("drops past rows but keeps future rows without a Today badge on Sunday", async () => {
    freezeClock(SUNDAY_AFTER_JULY4);
    stubCatalystsFetch({
      scan_time: "2026-07-02T23:07:38Z",
      count: 2,
      catalysts: [
        catRow("2026-07-03", 0, { title: "Stale Friday print" }),
        catRow("2026-07-07", 0, { title: "ISM services" }),
      ],
    });
    render(<CatalystCard />);
    await waitFor(() => expect(screen.getByText("ISM services")).toBeTruthy());
    expect(screen.queryByText("Stale Friday print")).toBeNull();
    expect(screen.queryByText("Today")).toBeNull();
    expect(screen.getByText("2d")).toBeTruthy();
  });

  it("does not render economic releases that already occurred today", async () => {
    freezeClock("2026-08-07T21:59:00Z");
    stubCatalystsFetch({
      scan_time: "2026-08-07T20:00:00Z",
      count: 2,
      catalysts: [
        catRow("2026-08-07", 0, { title: "Employment report", event_time: "2026-08-07T12:30:00Z" }),
        catRow("2026-08-07", 0, { title: "Evening speaker", event_time: "2026-08-07T22:30:00Z" }),
      ],
    });

    render(<CatalystCard />);
    await waitFor(() => expect(screen.getByText("Evening speaker")).toBeTruthy());
    expect(screen.queryByText("Employment report")).toBeNull();
  });

  it("renders the empty state when no catalysts are present", async () => {
    stubCatalystsFetch({ missing: true, count: 0, catalysts: [] });
    render(<CatalystCard />);
    await waitFor(() => expect(screen.getByText(/no upcoming catalysts/i)).toBeTruthy());
  });

  it("surfaces an error when the fetch fails", async () => {
    stubCatalystsFetch("boom", 500);
    render(<CatalystCard />);
    await waitFor(() => expect(screen.getByText(/failed/i)).toBeTruthy());
  });

  it("renders no raw hex colors", async () => {
    freezeClock(MONDAY_REGULAR);
    stubCatalystsFetch({
      scan_time: "2026-07-06T10:30:00Z",
      count: 1,
      catalysts: [
        catRow("2026-07-06", 0, { ticker: "AAPL", type: "earnings", title: "APPLE INC", source: "earnings_premarket" }),
      ],
    });
    const { container } = render(<CatalystCard />);
    await waitFor(() => expect(screen.getByText("AAPL")).toBeTruthy());
    expect(container.innerHTML).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
  });
});
