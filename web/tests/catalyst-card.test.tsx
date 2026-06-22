/**
 * @vitest-environment jsdom
 *
 * F3 — catalyst feed UI guards.
 *
 * Behaviours pinned:
 *  - catalystBadge() days-to-catalyst label + urgency bucket are correct
 *  - CatalystCard renders rows from mocked data (ticker, title, badge)
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
});

describe("catalystBadge", () => {
  it("labels today as Today", () => {
    expect(catalystBadge(0).label).toBe("Today");
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
  const okPayload = {
    scan_time: "2026-06-21T12:00:00Z",
    count: 2,
    catalysts: [
      { ticker: "AAPL", type: "earnings", title: "APPLE INC", date: "2026-06-23", source: "earnings_premarket", days_until: 2 },
      { ticker: null, type: "economic", title: "PCE index", date: "2026-06-26", source: "economic", days_until: 5 },
    ],
  };

  it("renders rows from the loaded payload", async () => {
    global.fetch = vi.fn(async () => new Response(JSON.stringify(okPayload), { status: 200 })) as typeof fetch;
    render(<CatalystCard />);
    await waitFor(() => expect(screen.getByText("AAPL")).toBeTruthy());
    expect(screen.getByText("PCE index")).toBeTruthy();
    expect(screen.getByText("2d")).toBeTruthy();
  });

  it("renders the empty state when no catalysts are present", async () => {
    global.fetch = vi.fn(async () =>
      new Response(JSON.stringify({ missing: true, count: 0, catalysts: [] }), { status: 200 }),
    ) as typeof fetch;
    render(<CatalystCard />);
    await waitFor(() => expect(screen.getByText(/no upcoming catalysts/i)).toBeTruthy());
  });

  it("surfaces an error when the fetch fails", async () => {
    global.fetch = vi.fn(async () => new Response("boom", { status: 500 })) as typeof fetch;
    render(<CatalystCard />);
    await waitFor(() => expect(screen.getByText(/failed/i)).toBeTruthy());
  });

  it("renders no raw hex colors", async () => {
    global.fetch = vi.fn(async () => new Response(JSON.stringify(okPayload), { status: 200 })) as typeof fetch;
    const { container } = render(<CatalystCard />);
    await waitFor(() => expect(screen.getByText("AAPL")).toBeTruthy());
    expect(container.innerHTML).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
  });
});
