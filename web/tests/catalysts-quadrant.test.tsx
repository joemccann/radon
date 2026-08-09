/**
 * @vitest-environment jsdom
 *
 * CatalystsQuadrant — grouped upcoming catalysts. Pinned behaviours:
 *  - TODAY / THIS WEEK / POSITIONS grouping with the position join
 *  - rail derives UPDATED time + event count from the payload
 *  - fossil snapshots (past-only rows) render the empty state
 */
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";

import CatalystsQuadrant from "@/components/dashboard/CatalystsQuadrant";
import type { CatalystData } from "@/lib/useCatalysts";

let payload: CatalystData | null = null;

vi.mock("@/lib/useCatalysts", async (importOriginal) => {
  const mod = await importOriginal<typeof import("@/lib/useCatalysts")>();
  return {
    ...mod,
    useCatalysts: () => ({ data: payload, isLoading: false, error: null, refresh: vi.fn() }),
  };
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  payload = null;
});

function freeze(instant: string) {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  vi.setSystemTime(new Date(instant));
}

describe("CatalystsQuadrant", () => {
  it("groups rows and joins held tickers into POSITIONS", () => {
    freeze("2026-08-04T14:00:00Z"); // Tuesday 10:00 ET
    payload = {
      scan_time: "2026-08-04T10:30:00Z",
      count: 3,
      catalysts: [
        { ticker: null, type: "economic", title: "NFIB optimism index", date: "2026-08-04", source: "economic", days_until: 0 },
        { ticker: null, type: "economic", title: "Core CPI", date: "2026-08-07", source: "economic", days_until: 3 },
        { ticker: "MCHP", type: "earnings", title: "MCHP earnings", date: "2026-08-04", source: "earnings_afterhours", days_until: 0 },
      ],
    };
    render(<CatalystsQuadrant positionTickers={new Set(["MCHP"])} />);
    expect(screen.getByText("TODAY")).toBeTruthy();
    expect(screen.getByText("THIS WEEK")).toBeTruthy();
    expect(screen.getByText("POSITIONS")).toBeTruthy();
    expect(screen.getByText("NFIB optimism index")).toBeTruthy();
    expect(screen.getByText(/MCHP · MCHP earnings/)).toBeTruthy();
    expect(screen.getByText("ER")).toBeTruthy();
    expect(screen.getAllByText("ECON")).toHaveLength(2);
  });

  it("derives the rail from the payload", () => {
    freeze("2026-08-04T14:00:00Z");
    payload = {
      scan_time: "2026-08-04T10:30:00Z",
      count: 1,
      catalysts: [
        { ticker: null, type: "economic", title: "Core CPI", date: "2026-08-07", source: "economic", days_until: 3 },
      ],
    };
    render(<CatalystsQuadrant positionTickers={new Set()} />);
    const rail = document.querySelector(".panel-meta-rail");
    expect(rail?.textContent).toContain("1");
  });

  it("renders the empty state for a fossil snapshot", () => {
    freeze("2026-08-04T14:00:00Z");
    payload = {
      scan_time: "2026-07-01T10:30:00Z",
      count: 1,
      catalysts: [
        { ticker: null, type: "economic", title: "Old print", date: "2026-07-01", source: "economic", days_until: 0 },
      ],
    };
    render(<CatalystsQuadrant positionTickers={new Set()} />);
    expect(screen.getByText(/No upcoming catalysts/)).toBeTruthy();
  });
});
