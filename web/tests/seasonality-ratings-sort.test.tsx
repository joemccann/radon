/**
 * @vitest-environment jsdom
 */

import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import SeasonalityTab from "@/components/ticker-detail/SeasonalityTab";
import RatingsTab from "@/components/ticker-detail/RatingsTab";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function month(n: number, avg: number, win: number) {
  return {
    month: n,
    avg_change: avg,
    median_change: avg,
    max_change: avg + 0.02,
    min_change: avg - 0.02,
    positive_closes: 6,
    positive_months_perc: win,
    years: 10,
  };
}

describe("SeasonalityTab sort", () => {
  it("reorders monthly detail when Avg is clicked", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          source: "uw",
          data: [month(1, 0.01, 0.55), month(2, 0.09, 0.7), month(3, -0.04, 0.4)],
        }),
      }),
    );
    render(<SeasonalityTab ticker="AAPL" active />);
    await waitFor(() => expect(screen.getByRole("table")).toBeTruthy());
    const first = () => within(screen.getByRole("table")).getAllByRole("row")[1].textContent ?? "";
    expect(first()).toContain("January");
    fireEvent.click(screen.getByRole("columnheader", { name: /^avg$/i }));
    expect(first()).toContain("March");
    expect(screen.getByRole("columnheader", { name: /^avg$/i }).getAttribute("aria-sort")).toBe("ascending");
  });
});

describe("RatingsTab sort", () => {
  it("reorders analyst actions when Firm is clicked", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          ticker: "AAPL",
          recommendation: "buy",
          analyst_count: 3,
          recent_changes: [
            { date: "2026-06-01", firm: "MS", action: "upgrade", to_grade: "Buy" },
            { date: "2026-06-02", firm: "BofA", action: "downgrade", to_grade: "Hold" },
            { date: "2026-06-03", firm: "GS", action: "init", to_grade: "Buy" },
          ],
        }),
      }),
    );
    render(<RatingsTab ticker="AAPL" active />);
    await waitFor(() => expect(screen.getByRole("table")).toBeTruthy());
    const first = () => within(screen.getByRole("table")).getAllByRole("row")[1].textContent ?? "";
    expect(first()).toContain("MS");
    fireEvent.click(screen.getByRole("columnheader", { name: /^firm$/i }));
    expect(first()).toContain("BofA");
    expect(screen.getByRole("columnheader", { name: /^firm$/i }).getAttribute("aria-sort")).toBe("ascending");
  });
});
