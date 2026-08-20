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

describe("ScannerModeTabs mobile navigation", () => {
  function stripAndShell() {
    const strip = screen.getByRole("tablist");
    const shell = strip.parentElement as HTMLElement;
    return { strip, shell };
  }

  it("exposes overflow affordance state on the shell as data attributes", () => {
    render(<ScannerModeTabs mode="flow" onModeChange={() => {}} counts={{}} />);
    const { strip, shell } = stripAndShell();
    expect(shell.className).toContain("scanner-mode-tabs-shell");

    Object.defineProperty(strip, "scrollWidth", { configurable: true, value: 900 });
    Object.defineProperty(strip, "clientWidth", { configurable: true, value: 393 });
    strip.scrollLeft = 0;
    fireEvent.scroll(strip);
    expect(shell.getAttribute("data-overflow-left")).toBe("false");
    expect(shell.getAttribute("data-overflow-right")).toBe("true");

    strip.scrollLeft = 200;
    fireEvent.scroll(strip);
    expect(shell.getAttribute("data-overflow-left")).toBe("true");
    expect(shell.getAttribute("data-overflow-right")).toBe("true");

    strip.scrollLeft = 507;
    fireEvent.scroll(strip);
    expect(shell.getAttribute("data-overflow-left")).toBe("true");
    expect(shell.getAttribute("data-overflow-right")).toBe("false");
  });

  it("uses a roving tabindex: only the selected tab is in the tab order", () => {
    render(<ScannerModeTabs mode="theta" onModeChange={() => {}} counts={{}} />);
    for (const tab of screen.getAllByRole("tab")) {
      const selected = tab.getAttribute("aria-selected") === "true";
      expect(tab.tabIndex).toBe(selected ? 0 : -1);
    }
  });

  it("moves focus with arrow keys, wrapping at both ends", () => {
    render(<ScannerModeTabs mode="flow" onModeChange={() => {}} counts={{}} />);
    const tabs = screen.getAllByRole("tab");
    tabs[0].focus();

    fireEvent.keyDown(screen.getByRole("tablist"), { key: "ArrowRight" });
    expect(document.activeElement).toBe(tabs[1]);

    fireEvent.keyDown(screen.getByRole("tablist"), { key: "ArrowLeft" });
    fireEvent.keyDown(screen.getByRole("tablist"), { key: "ArrowLeft" });
    expect(document.activeElement).toBe(tabs[tabs.length - 1]);

    fireEvent.keyDown(screen.getByRole("tablist"), { key: "ArrowRight" });
    expect(document.activeElement).toBe(tabs[0]);
  });

  it("Home and End jump to the first and last tab", () => {
    render(<ScannerModeTabs mode="flow" onModeChange={() => {}} counts={{}} />);
    const tabs = screen.getAllByRole("tab");
    tabs[0].focus();

    fireEvent.keyDown(screen.getByRole("tablist"), { key: "End" });
    expect(document.activeElement).toBe(tabs[tabs.length - 1]);

    fireEvent.keyDown(screen.getByRole("tablist"), { key: "Home" });
    expect(document.activeElement).toBe(tabs[0]);
  });

  it("arrow navigation does not fire onModeChange (manual activation)", () => {
    const onModeChange = vi.fn();
    render(<ScannerModeTabs mode="flow" onModeChange={onModeChange} counts={{}} />);
    screen.getAllByRole("tab")[0].focus();
    fireEvent.keyDown(screen.getByRole("tablist"), { key: "ArrowRight" });
    expect(onModeChange).not.toHaveBeenCalled();
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
