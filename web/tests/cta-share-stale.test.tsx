/**
 * @vitest-environment jsdom
 *
 * Bug 2026-06-12: the CTA "Share to X" modal silently presented yesterday's
 * report (cards + tweet dated 06-11) while the page itself showed live 06-12
 * data. The share payload now carries a freshness verdict ({stale, data_date,
 * expected_date}) and the modal must surface it instead of silently shipping
 * a stale report.
 */

import React from "react";
import { cleanup, render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { readFile } from "fs/promises";
import path from "path";

import ShareReportModal from "../components/ShareReportModal";

// Make React flush renders synchronously inside testing-library's act()
// wrappers — otherwise concurrent-mode work scheduled via setImmediate can
// fire after this file's jsdom env is torn down, crashing a later node-env
// test file in the same worker ("window is not defined").
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const PROJECT_ROOT = path.resolve(__dirname, "../..");

vi.mock("@/lib/useDialogChrome", () => ({
  useDialogChrome: () => ({ panelRef: { current: null } }),
}));

function mockShareFetch(shareResponse: Record<string, unknown>) {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    if (init?.method === "POST") {
      return {
        ok: true,
        json: async () => shareResponse,
      } as unknown as Response;
    }
    return {
      ok: true,
      text: async () => "<html><body>preview</body></html>",
    } as unknown as Response;
  });
}

describe("ShareReportModal stale-data warning", () => {
  beforeEach(() => {
    vi.stubGlobal("URL", Object.assign(URL, {
      createObjectURL: vi.fn(() => "blob:mock"),
      revokeObjectURL: vi.fn(),
    }));
  });

  afterEach(async () => {
    cleanup();
    // Drain react-dom's scheduled work while jsdom is still alive so no
    // immediate fires inside a later node-env test file in the same worker.
    await new Promise((resolve) => setImmediate(resolve));
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("renders a visible STALE warning when the share payload is behind the expected date", async () => {
    vi.stubGlobal("fetch", mockShareFetch({
      preview_path: "/reports/tweet-cta-2026-06-11.html",
      stale: true,
      data_date: "2026-06-11",
      expected_date: "2026-06-12",
    }));

    render(
      <ShareReportModal
        modalTitle="CTA REPORT — SHARE TO X"
        shareEndpoint="/api/menthorq/cta/share"
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /share to x/i }));

    await waitFor(() => {
      expect(screen.getByText(/STALE/i)).toBeTruthy();
    });
    expect(screen.getByText(/2026-06-11/)).toBeTruthy();
    expect(screen.getByText(/2026-06-12/)).toBeTruthy();
  });

  it("shows no stale warning when the payload matches the expected date", async () => {
    vi.stubGlobal("fetch", mockShareFetch({
      preview_path: "/reports/tweet-cta-2026-06-12.html",
      stale: false,
      data_date: "2026-06-12",
      expected_date: "2026-06-12",
    }));

    render(
      <ShareReportModal
        modalTitle="CTA REPORT — SHARE TO X"
        shareEndpoint="/api/menthorq/cta/share"
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /share to x/i }));

    await waitFor(() => {
      expect(screen.getByRole("dialog")).toBeTruthy();
    });
    expect(screen.queryByText(/STALE/i)).toBeNull();
  });
});

describe("generate_cta_share.py share source contract", () => {
  it("reads the Turso menthorq_cta row (same source as the CTA page) before the disk glob", async () => {
    const content = await readFile(
      path.join(PROJECT_ROOT, "scripts", "generate_cta_share.py"),
      "utf-8",
    );
    expect(content).toContain("menthorq_cta");
    expect(content).toContain("latest_closed_trading_day");
  });
});

describe("CTA route background sync observability", () => {
  it("logs spawn failure and non-zero exits instead of swallowing them", async () => {
    const content = await readFile(
      path.join(PROJECT_ROOT, "web", "app", "api", "menthorq", "cta", "route.ts"),
      "utf-8",
    );
    expect(content).toContain("background sync spawn failed");
    expect(content).toContain("background sync exited");
  });
});
