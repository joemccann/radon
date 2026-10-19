/**
 * @vitest-environment jsdom
 */

import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import SeasonalityTab from "@/components/ticker-detail/SeasonalityTab";
import { buildDemoSeasonality } from "@/lib/demo/fixtures/tickerInfo";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("SeasonalityTab source badge", () => {
  it("labels the demo sample as sample data, not a vendor source", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, json: async () => buildDemoSeasonality("AAPL") }),
    );
    render(<SeasonalityTab ticker="AAPL" active />);
    await waitFor(() => expect(screen.getByRole("table")).toBeTruthy());
    expect(screen.getByText("SAMPLE DATA")).toBeTruthy();
    expect(screen.queryByText(/EQUITYCLOCK/)).toBeNull();
  });
});
