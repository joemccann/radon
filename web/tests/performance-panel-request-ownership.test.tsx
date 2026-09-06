/** @vitest-environment jsdom */
import React from "react";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { goldenOkPayload } from "./fixtures/performanceScenarios";

const viewport = vi.hoisted(() => ({ mobile: true }));
vi.mock("@/lib/useViewport", () => ({
  useViewport: () => ({ isMobile: viewport.mobile, hasMounted: true }),
}));

import PerformancePanel from "../components/PerformancePanel";

describe("performance request ownership", () => {
  beforeEach(() => {
    viewport.mobile = true;
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(goldenOkPayload()), { status: 200 })));
  });
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

  it("uses one request owner on mobile, including across responsive layout changes", async () => {
    const { rerender } = render(<PerformancePanel />);
    await waitFor(() => expect(screen.getByTestId("performance-source-pill")).toBeTruthy());
    expect(screen.getByTestId("performance-panel").getAttribute("data-mobile")).toBe("true");
    expect(fetch).toHaveBeenCalledTimes(1);

    viewport.mobile = false;
    rerender(<PerformancePanel />);
    await waitFor(() => expect(screen.getByTestId("performance-panel").getAttribute("data-mobile")).toBeNull());
    viewport.mobile = true;
    rerender(<PerformancePanel />);
    await waitFor(() => expect(screen.getByTestId("performance-panel").getAttribute("data-mobile")).toBe("true"));
    expect(fetch).toHaveBeenCalledTimes(1);
  });
});
