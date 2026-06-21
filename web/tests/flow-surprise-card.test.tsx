/**
 * @vitest-environment jsdom
 *
 * Unit coverage for FlowSurpriseCard: EXCESS/DEFICIT chips render with the
 * right copy, the empty-state path shows the "No flow surprises yet" message,
 * and the route GET shape is sane (results[] + empty-state on missing file).
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import type { FlowSurpriseData } from "../lib/useFlowSurprise";

let mockReturn: {
  data: FlowSurpriseData | null;
  isLoading: boolean;
  error: string | null;
  refresh: () => void;
};

vi.mock("../lib/useFlowSurprise", async () => {
  const actual = await vi.importActual<typeof import("../lib/useFlowSurprise")>(
    "../lib/useFlowSurprise",
  );
  return {
    ...actual,
    useFlowSurprise: () => mockReturn,
  };
});

import { FlowSurpriseCard } from "../components/dashboard/FlowSurpriseCard";

function dataWith(results: FlowSurpriseData["results"]): FlowSurpriseData {
  return {
    results,
    metric: "flow_strength",
    scan_time: "2026-06-20T18:00:00Z",
    count_ok: results.length,
    skipped: 0,
  };
}

beforeEach(() => {
  mockReturn = { data: null, isLoading: false, error: null, refresh: vi.fn() };
});

afterEach(() => {
  cleanup();
});

describe("FlowSurpriseCard", () => {
  it("renders EXCESS and DEFICIT chips with ticker + actual vs expected", () => {
    mockReturn.data = dataWith([
      {
        ticker: "AAPL",
        metric: "flow_strength",
        status: "ok",
        flag: "EXCESS",
        actual: 8.2,
        expected_median: 3.1,
        surprise_pit: 0.97,
      },
      {
        ticker: "TSLA",
        metric: "flow_strength",
        status: "ok",
        flag: "DEFICIT",
        actual: 0.4,
        expected_median: 4.0,
        surprise_pit: 0.03,
      },
    ]);

    render(<FlowSurpriseCard />);

    expect(screen.getByText("Flow Surprise")).toBeTruthy();
    expect(screen.getByText("EXCESS")).toBeTruthy();
    expect(screen.getByText("DEFICIT")).toBeTruthy();
    expect(screen.getByText("AAPL")).toBeTruthy();
    expect(screen.getByText("TSLA")).toBeTruthy();
    // actual vs expected line for AAPL
    expect(screen.getByText("8.2 vs 3.1")).toBeTruthy();
    // surprise_pit rendered as a 0-100 percentile
    expect(screen.getByText("97")).toBeTruthy();
    expect(screen.getByText("3")).toBeTruthy();
  });

  it("renders the empty state when there are no results", () => {
    mockReturn.data = dataWith([]);
    render(<FlowSurpriseCard />);
    expect(screen.getByText("No flow surprises yet")).toBeTruthy();
  });

  it("shows the loading state while sampling", () => {
    mockReturn = { data: null, isLoading: true, error: null, refresh: vi.fn() };
    render(<FlowSurpriseCard />);
    expect(screen.getByText("Sampling…")).toBeTruthy();
  });

  it("renders the error state", () => {
    mockReturn = { data: null, isLoading: false, error: "boom", refresh: vi.fn() };
    render(<FlowSurpriseCard />);
    expect(screen.getByText("boom")).toBeTruthy();
  });

  it("uses no em dashes in the empty-state copy", () => {
    mockReturn.data = dataWith([]);
    render(<FlowSurpriseCard />);
    expect(screen.getByText("No flow surprises yet").textContent).not.toContain("—");
  });
});

describe("flow-surprise route shape (static contract)", () => {
  const src = readFileSync(
    join(__dirname, "..", "app", "api", "flow-surprise", "route.ts"),
    "utf8",
  );

  it("opts out of static caching and pins the node runtime", () => {
    expect(src).toMatch(/export\s+const\s+dynamic\s*=\s*["']force-dynamic["']/);
    expect(src).toMatch(/export\s+const\s+runtime\s*=\s*["']nodejs["']/);
  });

  it("returns an empty-state with missing:true (not a 4xx) on a missing file", () => {
    expect(src).toMatch(/missing:\s*true/);
    expect(src).not.toMatch(/status:\s*4\d\d/);
  });

  it("returns the {results, metric, scan_time, count_ok, skipped, cache_meta} envelope", () => {
    for (const key of ["results", "metric", "scan_time", "count_ok", "skipped", "cache_meta"]) {
      expect(src).toContain(key);
    }
  });
});
