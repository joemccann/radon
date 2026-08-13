/**
 * @vitest-environment jsdom
 */
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { ScannerModeTabs } from "../components/ScannerModeTabs";
import { SigMeter } from "../components/SigMeter";

afterEach(cleanup);

describe("ScannerModeTabs", () => {
  it("renders all seven modes with the existing classes and roles", () => {
    render(<ScannerModeTabs mode="flow" onModeChange={() => {}} counts={{}} />);
    const tabs = screen.getAllByRole("tab");
    expect(tabs).toHaveLength(7);
    expect(tabs[0].className).toContain("scanner-mode-tab--active");
    expect(tabs[1].className).toBe("scanner-mode-tab");
    expect(screen.getByRole("tab", { name: "VOL CONE" })).toBeTruthy();
    expect(screen.getByRole("tablist").getAttribute("aria-label")).toBe("Scanner mode");
  });

  it("switches modes through the callback", () => {
    const onModeChange = vi.fn();
    render(<ScannerModeTabs mode="flow" onModeChange={onModeChange} counts={{}} />);
    fireEvent.click(screen.getByRole("tab", { name: /GARCH/i }));
    expect(onModeChange).toHaveBeenCalledWith("garch");
    fireEvent.click(screen.getByRole("tab", { name: /VOL CONE/i }));
    expect(onModeChange).toHaveBeenCalledWith("vol-cone");
  });

  it("shows a hot count chip when a tab has results", () => {
    render(
      <ScannerModeTabs
        mode="flow"
        onModeChange={() => {}}
        counts={{ flow: 2, leap: 0, garch: 1 }}
      />,
    );
    const flowChip = screen.getByTestId("scanner-tab-count-flow");
    expect(flowChip.textContent).toBe("2");
    expect(flowChip.getAttribute("data-hot")).toBe("true");
    const leapChip = screen.getByTestId("scanner-tab-count-leap");
    expect(leapChip.textContent).toBe("0");
    expect(leapChip.getAttribute("data-hot")).toBe("false");
  });

  it("renders no chip when a count is unknown", () => {
    render(<ScannerModeTabs mode="flow" onModeChange={() => {}} counts={{ flow: 2 }} />);
    expect(screen.queryByTestId("scanner-tab-count-theta")).toBeNull();
    expect(screen.queryByTestId("scanner-tab-count-discover")).toBeNull();
  });
});

describe("SigMeter", () => {
  it("renders a clamped fill width with the tone class", () => {
    render(<SigMeter value={95} tone="pos" />);
    const meter = screen.getByTestId("sig-meter");
    expect(meter.className).toContain("sig-meter--pos");
    const fill = meter.querySelector("i") as HTMLElement;
    expect(fill.style.width).toBe("95%");
  });

  it("clamps out-of-range values", () => {
    render(<SigMeter value={140} tone="neg" />);
    const fill = screen.getByTestId("sig-meter").querySelector("i") as HTMLElement;
    expect(fill.style.width).toBe("100%");
  });

  it("renders nothing for null values", () => {
    const { container } = render(<SigMeter value={null} tone="mut" />);
    expect(container.innerHTML).toBe("");
  });
});
