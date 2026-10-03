/** @vitest-environment jsdom */
/** REL-108 / R-317: a failed report request cannot pin the shared owner. */
import React from "react";
import {act, cleanup, fireEvent, render, screen, waitFor} from "@testing-library/react";
import {afterEach, describe, expect, it, vi} from "vitest";
import ShareReportModal from "../components/ShareReportModal";
vi.mock("@/lib/useDialogChrome", () => ({useDialogChrome: () => ({panelRef: {current: null}})}));
afterEach(() => {cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks();});
const endpoint = "/api/menthorq/cta/share";
function mount(enabled = true) {
  render(<ShareReportModal modalTitle="Mock report" shareEndpoint={endpoint} enabled={enabled} />);
}

describe("report-share failure bounds", () => {
  it("does not issue a request while the share gate is closed", () => {
    const fetch = vi.fn(); vi.stubGlobal("fetch", fetch);
    mount(false);
    expect(screen.queryByRole("button", {name: "Share to X"})).toBeNull();
    expect(fetch).not.toHaveBeenCalled();
  });
  it("classifies HTML status before JSON parsing and releases the button", async () => {
    const response = new Response("<html>proxy unavailable</html>", {status: 502});
    const json = vi.spyOn(response, "json");
    const fetch = vi.fn().mockResolvedValue(response); vi.stubGlobal("fetch", fetch);
    mount(); fireEvent.click(screen.getByRole("button", {name: "Share to X"}));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("temporarily unavailable"));
    expect(json).not.toHaveBeenCalled();
    expect(fetch).toHaveBeenCalledExactlyOnceWith(endpoint, {method: "POST", signal: expect.any(AbortSignal)});
    expect((screen.getByRole("button", {name: "Share to X"}) as HTMLButtonElement).disabled).toBe(false);
  });
  it.each(["generation", "content"])("aborts a stalled %s within the shared deadline", async phase => {
    vi.useFakeTimers();
    const signals: AbortSignal[] = [];
    const fetch = vi.fn((url: string, init?: RequestInit) => {
      const signal = init?.signal as AbortSignal; signals.push(signal);
      if (phase === "content" && url === endpoint) return Promise.resolve({ok: true,
        json: async () => ({preview_path: "/reports/mock.html"})});
      return new Promise((_, reject) => signal.addEventListener("abort", () => reject(new DOMException("mock", "AbortError")), {once: true}));
    });
    vi.stubGlobal("fetch", fetch); mount();
    await act(async () => {fireEvent.click(screen.getByRole("button", {name: "Share to X"}));});
    expect(fetch).toHaveBeenNthCalledWith(1, endpoint, {method: "POST", signal: signals[0]});
    if (phase === "content") {
      expect(fetch).toHaveBeenNthCalledWith(2, `${endpoint}/content?path=%2Freports%2Fmock.html`, {signal: signals[0]});
      expect(signals[1]).toBe(signals[0]);
    }
    await act(async () => {await vi.advanceTimersByTimeAsync(30_001);});
    expect(signals[0].aborted).toBe(true);
    expect(screen.getByRole("alert").textContent).toContain("share request took too long");
    expect((screen.getByRole("button", {name: "Share to X"}) as HTMLButtonElement).disabled).toBe(false);
    expect(vi.getTimerCount()).toBe(0);
  });
});
