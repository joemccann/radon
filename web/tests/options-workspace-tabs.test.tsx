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

  it("keeps Net GEX as the first active tab and exposes planned measurements", () => {
    render(<OptionsWorkspacePanel symbol="MU" />);

    expect(screen.getByRole("tablist", { name: "Options measurements" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "Net GEX" }).getAttribute("aria-selected")).toBe("true");
    expect(screen.getByRole("tab", { name: /DEX/ }).getAttribute("aria-disabled")).toBe("true");
    expect(screen.getByRole("tab", { name: /Greeks/ }).getAttribute("aria-disabled")).toBe("true");
    expect(screen.getByTestId("net-gex-panel").textContent).toContain("MU");
  });

  it("uses the canonical URL when Net GEX is selected", () => {
    render(<OptionsWorkspacePanel symbol="MU" />);

    fireEvent.click(screen.getByRole("tab", { name: "Net GEX" }));
    expect(push).toHaveBeenCalledWith("/options/net-gex?symbol=MU");
  });
});
