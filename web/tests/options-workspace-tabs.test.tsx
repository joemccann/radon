/**
 * @vitest-environment jsdom
 */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const push = vi.fn();
let pathname = "/options/net-gex";

vi.mock("next/navigation", () => ({
  usePathname: () => pathname,
  useRouter: () => ({ push }),
}));

vi.mock("@/components/OptionsExposurePanel", () => ({
  default: ({ symbol }: { symbol: string }) => <div data-testid="net-gex-panel">Net GEX {symbol}</div>,
}));

import OptionsWorkspacePanel from "@/components/OptionsWorkspacePanel";

describe("OptionsWorkspacePanel", () => {
  afterEach(cleanup);

  beforeEach(() => {
    pathname = "/options/net-gex";
    push.mockReset();
  });

  it("requires an explicit ticker before mounting the Net GEX measurement", () => {
    render(<OptionsWorkspacePanel />);

    expect(screen.getByRole("tablist", { name: "Options measurements" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "Net GEX" }).getAttribute("aria-selected")).toBe("true");
    expect(screen.getByRole("tab", { name: /DEX/ }).getAttribute("aria-disabled")).toBe("true");
    expect(screen.getByRole("tab", { name: /Greeks/ }).getAttribute("aria-disabled")).toBe("true");
    expect(screen.getByRole("search", { name: "Options ticker" })).toBeTruthy();
    expect(screen.queryByTestId("net-gex-panel")).toBeNull();
  });

  it("rejects an invalid ticker without starting a measurement", () => {
    render(<OptionsWorkspacePanel />);

    fireEvent.change(screen.getByLabelText("Ticker symbol"), { target: { value: "!!!" } });
    fireEvent.click(screen.getByRole("button", { name: "Load exposure" }));

    expect(screen.getByRole("alert").textContent).toContain("Enter a valid ticker symbol");
    expect(push).not.toHaveBeenCalled();
    expect(screen.queryByTestId("net-gex-panel")).toBeNull();
  });

  it("normalizes a valid ticker and starts the canonical Net GEX measurement", () => {
    render(<OptionsWorkspacePanel />);

    fireEvent.change(screen.getByLabelText("Ticker symbol"), { target: { value: "mu" } });
    fireEvent.click(screen.getByRole("button", { name: "Load exposure" }));

    expect(push).toHaveBeenCalledWith("/options/net-gex?symbol=MU");
    expect(screen.getByTestId("net-gex-panel").textContent).toContain("MU");
  });

  it("uses a direct valid ticker deep link without injecting a default", () => {
    render(<OptionsWorkspacePanel symbol="AAPL" />);

    expect(screen.getByTestId("net-gex-panel").textContent).toContain("AAPL");
  });
});
