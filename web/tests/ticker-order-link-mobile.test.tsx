/** @vitest-environment jsdom */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import TickerWorkspace from "../components/TickerWorkspace";
import TickerDetailContent from "../components/TickerDetailContent";
const state = vi.hoisted(() => ({ mobile: true, mounted: true, params: new URLSearchParams("tab=order&src=research"), replace: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace: state.replace, back: vi.fn() }), useSearchParams: () => state.params }));
vi.mock("@/lib/useViewport", () => ({ useViewport: () => ({ isMobile: state.mobile, hasMounted: state.mounted }) }));
vi.mock("@/lib/useStockState", () => ({ useStockState: () => ({ fallback: null }) }));
vi.mock("@/lib/TickerDetailContext", () => ({ useTickerDetailOptional: () => null, useTickerDetail: () => ({ getPrices: () => ({}), getFundamentals: () => ({}), getDepths: () => ({}), getTape: () => ({}), portfolio: null, orders: null, setDepthSymbols: vi.fn() }) }));
vi.mock("../components/ticker-detail/AssetCockpit", () => ({ default: ({ activeDeck, onDeckChange }: { activeDeck: string | null; onDeckChange: (value: string | null) => void }) => <div><output aria-label="Active deck">{activeDeck ?? "docked"}</output><button onClick={() => onDeckChange(null)}>Close deck</button></div> }));
afterEach(() => { cleanup(); state.mobile = true; state.mounted = true; state.params = new URLSearchParams("tab=order&src=research"); vi.clearAllMocks(); });
describe("legacy order link viewport handoff", () => {
  it("opens the mobile order deck for explicit legacy order links", () => { render(<TickerWorkspace ticker="AAPL" theme="dark" />); expect(screen.getByLabelText("Active deck").textContent).toBe("o"); });
  it("keeps the desktop ticket docked and does not open a duplicate deck", () => { state.mobile = false; render(<TickerWorkspace ticker="AAPL" theme="dark" />); expect(screen.getByLabelText("Active deck").textContent).toBe("docked"); });
  it("keeps the SSR fallback docked until mobile viewport hydration", () => { state.mounted = false; const view = render(<TickerWorkspace ticker="AAPL" theme="dark" />); expect(screen.getByLabelText("Active deck").textContent).toBe("docked"); state.mounted = true; view.rerender(<TickerWorkspace ticker="AAPL" theme="dark" />); expect(screen.getByLabelText("Active deck").textContent).toBe("o"); });
  it("respects an explicit reference deck over the legacy order parameter", () => { state.params = new URLSearchParams("tab=order&deck=c"); render(<TickerWorkspace ticker="AAPL" theme="dark" />); expect(screen.getByLabelText("Active deck").textContent).toBe("c"); });
  it("allows the requested mobile deck to close without reopening", () => { const onTabChange = vi.fn(); render(<TickerDetailContent ticker="AAPL" positionId={null} activeTab="order" onTabChange={onTabChange} prices={{}} fundamentals={{}} portfolio={null} orders={null} theme="dark" />); fireEvent.click(screen.getByRole("button", { name: "Close deck" })); expect(screen.getByLabelText("Active deck").textContent).toBe("docked"); expect(onTabChange).toHaveBeenCalledWith("book"); });
});
