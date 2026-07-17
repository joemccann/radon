/**
 * @vitest-environment jsdom
 */

import React from "react";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import OptionsExposurePanel from "@/components/OptionsExposurePanel";
import { EXPOSURE_FIXTURE } from "./options-exposure-fixture";

const useOptionsExposureMock = vi.fn();

vi.mock("@/lib/useOptionsExposure", () => ({
  useOptionsExposure: (...args: unknown[]) => useOptionsExposureMock(...args),
}));

describe("OptionsExposurePanel", () => {
  beforeEach(() => {
    useOptionsExposureMock.mockReturnValue({
      data: EXPOSURE_FIXTURE,
      loading: false,
      error: null,
      refresh: vi.fn(),
    });
  });

  afterEach(() => cleanup());

  it("renders the default instrument controls and accessible exposure table", () => {
    render(<OptionsExposurePanel symbol="MU" />);

    expect(screen.getByRole("heading", { name: "Options exposure" })).toBeTruthy();
    expect((screen.getByLabelText("Exposure metric") as HTMLSelectElement).value).toBe("net_gex");
    expect((screen.getByLabelText("Strike range") as HTMLSelectElement).value).toBe("10");
    expect(screen.getByRole("button", { name: "EOD" }).getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByRole("button", { name: "All Expirations" }).getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByRole("table", { name: "MU options exposure by strike" })).toBeTruthy();
    expect(screen.getByText("Jul 16, 20:00 UTC")).toBeTruthy();
    expect(screen.getAllByRole("columnheader").map((cell) => cell.textContent)).toEqual([
      "Strike",
      "Levels",
      "Net GEX",
      "Put OI / Call OI",
    ]);
  });

  it("marks the nearest spot row and renders signed bar direction classes", () => {
    render(<OptionsExposurePanel symbol="MU" />);
    const spotRow = screen.getByTestId("exposure-row-100");
    expect(spotRow.getAttribute("data-spot")).toBe("true");
    expect(within(spotRow).getByText("SPOT")).toBeTruthy();
    expect(screen.getByTestId("exposure-bar-90").getAttribute("data-sign")).toBe("positive");
    expect(screen.getByTestId("exposure-bar-100").getAttribute("data-sign")).toBe("negative");
  });

  it("filters expiration locally without refetching", () => {
    render(<OptionsExposurePanel symbol="MU" />);
    fireEvent.click(screen.getByRole("button", { name: /Jul 17/ }));
    expect(screen.getByRole("button", { name: /Jul 17/ }).getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByTestId("exposure-value-90").textContent).toContain("-$1");
    expect(useOptionsExposureMock.mock.calls.every((call) => call[1] === "eod")).toBe(true);
  });

  it("switches metric semantics and frequency while preserving exact options", () => {
    render(<OptionsExposurePanel symbol="MU" />);
    fireEvent.change(screen.getByLabelText("Exposure metric"), { target: { value: "open_interest" } });
    expect(screen.getByRole("columnheader", { name: "Open Interest" })).toBeTruthy();
    expect(screen.getByTestId("exposure-value-100").textContent).toContain("+20");

    fireEvent.click(screen.getByRole("button", { name: "Intraday" }));
    expect(useOptionsExposureMock).toHaveBeenLastCalledWith("MU", "intraday");
  });

  it("toggles level overlays and labels partial payloads", () => {
    const partial = {
      ...EXPOSURE_FIXTURE,
      complete: false,
      levels: [
        ...EXPOSURE_FIXTURE.levels,
        { key: "min_1d" as const, label: "1D Min", value: null },
      ],
    };
    useOptionsExposureMock.mockReturnValue({ data: partial, loading: false, error: null, refresh: vi.fn() });
    render(<OptionsExposurePanel symbol="MU" />);

    expect(screen.getByText("PARTIAL")).toBeTruthy();
    expect(screen.getByText("HVL 100.00")).toBeTruthy();
    expect(screen.queryByText(/1D Min\s/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Toggle HVL level" }));
    expect(screen.queryByText("HVL 100.00")).toBeNull();
  });

  it("renders loading, error, unavailable, and empty measurement states", () => {
    useOptionsExposureMock.mockReturnValue({ data: null, loading: true, error: null, refresh: vi.fn() });
    const { rerender } = render(<OptionsExposurePanel symbol="MU" />);
    expect(screen.getByRole("status").textContent).toContain("Sampling options exposure");

    useOptionsExposureMock.mockReturnValue({ data: null, loading: false, error: "Feed unavailable", refresh: vi.fn() });
    rerender(<OptionsExposurePanel symbol="MU" />);
    expect(screen.getByRole("alert").textContent).toContain("Feed unavailable");

    useOptionsExposureMock.mockReturnValue({ data: null, loading: false, error: null, refresh: vi.fn() });
    rerender(<OptionsExposurePanel symbol="MU" />);
    expect(screen.getByRole("status").textContent).toContain("No exposure measurement returned");

    useOptionsExposureMock.mockReturnValue({
      data: { ...EXPOSURE_FIXTURE, strikes: [], cells: { ...EXPOSURE_FIXTURE.cells, strike_idx: [] } },
      loading: false,
      error: null,
      refresh: vi.fn(),
    });
    rerender(<OptionsExposurePanel symbol="MU" />);
    expect(screen.getByRole("status").textContent).toContain("No strikes match the current measurement controls");
  });

  it("uses responsive, overflow-safe structure", () => {
    const { container } = render(<OptionsExposurePanel symbol="MU" />);
    expect(container.querySelector('[data-testid="options-exposure-panel"]')?.getAttribute("data-responsive")).toBe("instrument");
    expect(container.querySelector('[data-testid="options-exposure-controls"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="options-exposure-table-wrap"]')).toBeTruthy();
  });
});
