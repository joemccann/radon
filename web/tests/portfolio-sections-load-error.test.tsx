/**
 * @vitest-environment jsdom
 *
 * 2026-10-18 demo sweep: with GET /api/portfolio failing (503) and no snapshot,
 * /portfolio showed a LOADING pill and "Waiting for portfolio data..." forever.
 * The shell's degraded banner is suppressed in demo mode, so nothing on the
 * page said the read had failed. A failed cold read must say so.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import PortfolioSections from "@/components/PortfolioSections";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
  usePathname: () => "/portfolio",
}));

vi.mock("@/components/InstrumentDetailModal", () => ({ default: () => null }));

afterEach(() => cleanup());

describe("PortfolioSections cold-load error", () => {
  it("shows LOADING while the first read is in flight", () => {
    render(<PortfolioSections portfolio={null} />);
    expect(screen.getByText("LOADING")).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("reports a failed first read instead of loading forever", () => {
    render(<PortfolioSections portfolio={null} loadError="Service Unavailable" />);
    expect(screen.queryByText("LOADING")).toBeNull();
    expect(screen.getByText("UNAVAILABLE")).toBeTruthy();
    expect(screen.queryByText("Waiting for portfolio data...")).toBeNull();
    expect(screen.getByRole("alert").textContent).toContain("Service Unavailable");
  });
});
