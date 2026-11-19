/** @vitest-environment jsdom */
/**
 * The Share to X request carries the reading the page is showing at click
 * time. 2026-11-19: the page showed CRI 51 while the cards showed 48.
 */
import React from "react";
import {cleanup, fireEvent, render, screen, waitFor} from "@testing-library/react";
import {afterEach, describe, expect, it, vi} from "vitest";
import ShareReportModal from "../components/ShareReportModal";
vi.mock("@/lib/useDialogChrome", () => ({useDialogChrome: () => ({panelRef: {current: null}})}));
afterEach(() => {cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks();});

const endpoint = "/api/regime/share";

function stubFetch() {
  const fetch = vi.fn().mockResolvedValue(new Response("{}", {status: 500}));
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

describe("ShareReportModal live reading", () => {
  it("posts the reading returned at click time as {live}", async () => {
    const fetch = stubFetch();
    let score = 48;
    const shareBody = vi.fn(() => ({cri: {score}}));
    render(<ShareReportModal modalTitle="Regime" shareEndpoint={endpoint} shareBody={shareBody} />);
    expect(fetch).not.toHaveBeenCalled();
    expect(shareBody).not.toHaveBeenCalled();

    score = 51;
    fireEvent.click(screen.getByRole("button", {name: "Share to X"}));
    await waitFor(() => expect(fetch).toHaveBeenCalled());
    expect(fetch).toHaveBeenCalledExactlyOnceWith("/api/regime/share", {
      method: "POST",
      signal: expect.any(AbortSignal),
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({live: {cri: {score: 51}}}),
    });
  });

  it("posts no body when there is no live reading", async () => {
    const fetch = stubFetch();
    render(<ShareReportModal modalTitle="Regime" shareEndpoint={endpoint} shareBody={() => null} />);
    fireEvent.click(screen.getByRole("button", {name: "Share to X"}));
    await waitFor(() => expect(fetch).toHaveBeenCalled());
    expect(fetch).toHaveBeenCalledExactlyOnceWith("/api/regime/share", {method: "POST", signal: expect.any(AbortSignal)});
  });
});
