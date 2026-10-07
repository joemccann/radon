/** @vitest-environment jsdom */
import React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

// Coverage-instrumented CI shards run this render well past waitFor's 1s default.
const SETTLE = { timeout: 10_000 };
import SharePnlButton from "../components/SharePnlButton";

const DATA = {
  description: "Closed NVDA Call", pnl: 100, pnlPct: 20, commission: 1,
  fillPrice: 6, entryPrice: 5, exitPrice: 6,
  entryTime: "2026-09-14T14:00:00Z", exitTime: "2026-09-16T15:00:00Z", time: "2026-09-16",
};

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  // A plain { ok, blob } keeps the Blob in jsdom's realm (File needs it there).
  fetchMock = vi.fn(async () => ({ ok: true, blob: async () => new Blob(["png"], { type: "image/png" }) }));
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  // jsdom has no Web Share API; tests that add one must not leak it.
  delete (navigator as { share?: unknown }).share;
  delete (navigator as { canShare?: unknown }).canShare;
});

function openPopover() {
  render(<SharePnlButton data={DATA} />);
  fireEvent.click(screen.getByRole("button", { name: "Share P&L" }));
  return screen.getByRole("button", { name: "Instagram Story" });
}

function requestedUrl(): URL {
  expect(fetchMock).toHaveBeenCalledTimes(1);
  return new URL(String(fetchMock.mock.calls[0][0]), "https://radon.run");
}

it("requests the 9:16 story plate and hands the PNG to the native share sheet", async () => {
  const share = vi.fn(async () => undefined);
  const canShare = vi.fn(() => true);
  Object.assign(navigator, { share, canShare });

  fireEvent.click(openPopover());

  await waitFor(() => expect(share).toHaveBeenCalledTimes(1), SETTLE);
  const url = requestedUrl();
  expect(url.pathname).toBe("/api/share/pnl");
  expect(url.searchParams.get("format")).toBe("story");
  expect(url.searchParams.get("description")).toBe("Closed NVDA Call");
  expect(url.searchParams.get("pnlPct")).toBe("20");
  // P&L $ is off by default, so the dollar figure must not leak into the plate.
  expect(url.searchParams.has("pnl")).toBe(false);

  const payload = share.mock.calls[0][0] as ShareData;
  expect(payload.files).toHaveLength(1);
  const file = payload.files![0];
  expect(file.type).toBe("image/png");
  expect(file.name).toBe("radon-pnl-story.png");
  // Text alongside files makes iOS drop Instagram from the share targets.
  expect(payload.text).toBeUndefined();
  expect(payload.url).toBeUndefined();
}, 20_000);

it("downloads the story PNG when the browser cannot share files", async () => {
  const createObjectURL = vi.fn(() => "blob:story");
  const revokeObjectURL = vi.fn();
  Object.assign(URL, { createObjectURL, revokeObjectURL });
  const clicks: HTMLAnchorElement[] = [];
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
    clicks.push(this);
  });

  fireEvent.click(openPopover());

  await waitFor(() => expect(clicks).toHaveLength(1), SETTLE);
  expect(requestedUrl().searchParams.get("format")).toBe("story");
  expect(clicks[0].download).toBe("radon-pnl-story.png");
  expect(clicks[0].href).toBe("blob:story");
}, 20_000);

it("treats a dismissed share sheet as a cancel, not an error", async () => {
  const share = vi.fn(async () => { throw new DOMException("cancelled", "AbortError"); });
  Object.assign(navigator, { share, canShare: () => true });

  fireEvent.click(openPopover());

  await waitFor(() => expect(share).toHaveBeenCalledTimes(1), SETTLE);
  await act(async () => {});
  expect(screen.queryByRole("alert")).toBeNull();
}, 20_000);

it("re-arms for a second tap when the browser expired the share gesture", async () => {
  const share = vi
    .fn()
    .mockRejectedValueOnce(new DOMException("no activation", "NotAllowedError"))
    .mockResolvedValueOnce(undefined);
  Object.assign(navigator, { share, canShare: () => true });

  fireEvent.click(openPopover());
  const retry = await screen.findByRole("button", { name: "Tap to share Story" }, SETTLE);
  fireEvent.click(retry);

  await waitFor(() => expect(share).toHaveBeenCalledTimes(2), SETTLE);
  // The second tap reuses the rendered PNG instead of re-requesting it.
  expect(fetchMock).toHaveBeenCalledTimes(1);
  expect((share.mock.calls[1][0] as ShareData).files![0].name).toBe("radon-pnl-story.png");
}, 20_000);

it("fires nothing while both metrics are unchecked", () => {
  const share = vi.fn();
  Object.assign(navigator, { share, canShare: () => true });

  const story = openPopover();
  fireEvent.click(screen.getByRole("checkbox", { name: "P&L %" }));
  expect((story as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(story);

  expect(fetchMock).not.toHaveBeenCalled();
  expect(share).not.toHaveBeenCalled();
});
