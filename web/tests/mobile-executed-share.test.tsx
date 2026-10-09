/** @vitest-environment jsdom */
import React from "react";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import MobileExecutedList from "../components/mobile/MobileExecutedList";
import type { PositionFillGroup } from "../components/WorkspaceSections";
import type { SharePnlData } from "../components/SharePnlButton";

function fill(side: string) {
  return {
    execId: `exec-${side}`,
    symbol: "CRWD",
    contract: { conId: 1, symbol: "CRWD", secType: "OPT", strike: 250, right: "P", expiry: "20261030" },
    side,
    quantity: 200,
    avgPrice: 6.55,
    commission: 69.76,
    realizedPNL: side === "SLD" ? 32310.98 : null,
    time: "2026-10-08T17:26:48Z",
    exchange: "SMART",
  } as PositionFillGroup["fills"][number];
}

const CLOSE_GROUP: PositionFillGroup = {
  id: "crwd-close",
  symbol: "CRWD",
  description: "Closed CRWD 10/30 (LONG $250 PUT)",
  isClosing: true,
  totalQuantity: 200,
  netPrice: 6.55,
  totalCommission: 69.76,
  totalPnL: 32310.98,
  time: "2026-10-08T17:26:48Z",
  fills: [fill("SLD")],
};

const OPEN_GROUP: PositionFillGroup = {
  ...CLOSE_GROUP,
  id: "crwd-open",
  description: "Opened CRWD 10/30 (LONG $270 CALL)",
  isClosing: false,
  totalPnL: null,
  fills: [fill("BOT")],
};

const SHARE: SharePnlData = {
  description: "Closed CRWD 10/30 (LONG $250 PUT)",
  pnl: 32310.98,
  pnlPct: 33.1,
  commission: 69.76,
  fillPrice: 6.55,
  entryPrice: 4.92,
  exitPrice: 6.55,
  entryTime: null,
  exitTime: "2026-10-08T17:26:48Z",
  time: "2026-10-08T17:26:48Z",
};

const shareDataFor = (group: PositionFillGroup) => (group.isClosing && group.totalPnL != null ? SHARE : null);

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  fetchMock = vi.fn(async () => new Response(new Blob(["png"], { type: "image/png" }), { status: 200 }));
  vi.stubGlobal("fetch", fetchMock);
  vi.stubGlobal("ClipboardItem", class { constructor(public items: unknown) {} });
  Object.defineProperty(navigator, "clipboard", { configurable: true, value: { write: vi.fn(async () => undefined) } });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("MobileExecutedList share on tap", () => {
  it("tapping a closed card expands inline share actions that request the P&L image", async () => {
    render(<MobileExecutedList groups={[CLOSE_GROUP]} shareDataFor={shareDataFor} />);
    expect(screen.queryByRole("button", { name: "Copy & Tweet" })).toBeNull();

    fireEvent.click(screen.getByTestId("mobile-executed-crwd-close"));
    const panel = screen.getByTestId("mobile-executed-crwd-close-share");
    expect(fetchMock).not.toHaveBeenCalled();

    fireEvent.click(within(panel).getByRole("checkbox", { name: "P&L $" }));
    expect(screen.queryByTestId("mobile-executed-crwd-close-share")).not.toBeNull();

    fireEvent.click(within(panel).getByRole("button", { name: "Copy" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const url = new URL(String(fetchMock.mock.calls[0][0]), "http://localhost");
    expect(url.pathname).toBe("/api/share/pnl");
    expect(url.searchParams.get("description")).toBe(SHARE.description);
    expect(url.searchParams.get("pnl")).toBe("32310.98");
    expect(url.searchParams.get("pnlPct")).toBe("33.1");
    expect(screen.queryByTestId("mobile-executed-crwd-close-share")).not.toBeNull();
  });

  it("disables share actions while both P&L toggles are off", () => {
    render(<MobileExecutedList groups={[CLOSE_GROUP]} shareDataFor={shareDataFor} />);
    fireEvent.click(screen.getByTestId("mobile-executed-crwd-close"));
    const panel = screen.getByTestId("mobile-executed-crwd-close-share");
    fireEvent.click(within(panel).getByRole("checkbox", { name: "P&L %" }));
    const copy = within(panel).getByRole("button", { name: "Copy" });
    expect((copy as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(copy);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("collapses again on a second tap", () => {
    render(<MobileExecutedList groups={[CLOSE_GROUP]} shareDataFor={shareDataFor} />);
    const card = screen.getByTestId("mobile-executed-crwd-close");
    fireEvent.click(card);
    expect(card.getAttribute("aria-expanded")).toBe("true");
    fireEvent.click(card);
    expect(screen.queryByTestId("mobile-executed-crwd-close-share")).toBeNull();
  });

  it("puts the story control outside the pressable card, ahead of the fill list", async () => {
    const leg = fill("SLD");
    const multi: PositionFillGroup = {
      ...CLOSE_GROUP,
      fills: [leg, { ...leg, execId: "exec-SLD-2", quantity: 50 }],
    };
    render(<MobileExecutedList groups={[multi]} shareDataFor={shareDataFor} />);
    fireEvent.click(screen.getByTestId("mobile-executed-crwd-close"));

    const card = screen.getByTestId("mobile-executed-crwd-close");
    const story = screen.getByRole("button", { name: "Instagram Story" });
    const fills = screen.getByTestId("mobile-executed-crwd-close-fills");
    expect(card.contains(story)).toBe(false);
    expect(story.closest(".m-card-press")).toBeNull();
    expect(story.closest("[role='button']")).toBeNull();
    expect(story.compareDocumentPosition(fills) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    fireEvent.click(story);
    expect(card.getAttribute("aria-expanded")).toBe("true");
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const url = new URL(String(fetchMock.mock.calls[0][0]), "http://localhost");
    expect(url.pathname).toBe("/api/share/pnl");
    expect(url.searchParams.get("format")).toBe("story");
    expect(card.getAttribute("aria-expanded")).toBe("true");
  });

it("opening fills expose no share actions", () => {
    render(<MobileExecutedList groups={[OPEN_GROUP]} shareDataFor={shareDataFor} />);
    const card = screen.getByTestId("mobile-executed-crwd-open");
    fireEvent.click(card);
    expect(screen.queryByTestId("mobile-executed-crwd-open-share")).toBeNull();
    expect(screen.queryByRole("button", { name: "Copy & Tweet" })).toBeNull();
  });
});
