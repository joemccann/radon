/** @vitest-environment jsdom */
import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

afterEach(cleanup);

const mockUseRegime = vi.fn();
const mockUseMenthorqCta = vi.fn();

vi.mock("@/lib/useRegime", () => ({
  useRegime: mockUseRegime,
}));

vi.mock("@/lib/useMenthorqCta", () => ({
  useMenthorqCta: mockUseMenthorqCta,
}));

vi.mock("../components/SortableCtaTable", () => ({
  default: ({ sectionKey }: { sectionKey: string }) =>
    React.createElement("div", { "data-testid": "sortable-cta-table" }, sectionKey),
}));

vi.mock("../components/InfoTooltip", () => ({
  default: () => React.createElement("span", { "data-testid": "info-tooltip" }, "?"),
}));

describe("components/CtaPage.tsx — freshness states", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.clearAllMocks();

    mockUseRegime.mockReturnValue({
      data: {
        cta: {
          exposure_pct: 92,
          forced_reduction_pct: 0,
          est_selling_bn: 1.4,
        },
      },
    });
  });

  it("renders a stale CTA warning when the cache is behind the latest closed trading day", async () => {
    mockUseMenthorqCta.mockReturnValue({
      loading: false,
      error: null,
      data: {
        date: "2026-03-10",
        fetched_at: "2026-03-10T22:05:00Z",
        source: "menthorq_s3_vision",
        tables: {
          main: [{ underlying: "SPX" }],
          index: [],
          commodity: [],
          currency: [],
        },
        cache_meta: {
          last_refresh: "2026-03-10T22:05:00.000Z",
          age_seconds: 61_200,
          is_stale: true,
          stale_threshold_seconds: null,
          target_date: "2026-03-11",
          latest_cache_date: "2026-03-10",
          stale_reason: "behind_target",
        },
        sync_status: {
          service: "cta-sync",
          status: "error",
          trigger: "launchd",
          target_date: "2026-03-11",
          started_at: "2026-03-11T22:05:00Z",
          finished_at: "2026-03-11T22:05:31Z",
          duration_ms: 31_000,
          attempt_count: 2,
          cache_path: null,
          error_type: "auth_rejected",
          error_excerpt: "Your username or password was incorrect",
          artifact_log_path: "logs/cta-sync-artifacts/cta-sync-20260311T220531.log",
        },
      },
    });

    const { default: CtaPage } = await import("../components/CtaPage");
    const { container } = render(React.createElement(CtaPage));
    const html = container.innerHTML;

    expect(html).toContain("CTA CACHE STALE");
    expect(html).toContain("2026-03-10");
    expect(html).toContain("2026-03-11");
    expect(screen.getByRole("alert").textContent).toContain("Your username or password was incorrect");
    expect(screen.getByRole("alert").closest("[data-toast-viewport]")).toBeTruthy();
    expect(html).not.toContain("Your username or password was incorrect");
  });

  it("omits the stale warning when the CTA cache is fresh", async () => {
    mockUseMenthorqCta.mockReturnValue({
      loading: false,
      error: null,
      data: {
        date: "2026-03-11",
        fetched_at: "2026-03-11T22:05:00Z",
        source: "menthorq_s3_vision",
        tables: {
          main: [{ underlying: "SPX" }],
          index: [],
          commodity: [],
          currency: [],
        },
        cache_meta: {
          last_refresh: "2026-03-11T22:05:00.000Z",
          age_seconds: 120,
          is_stale: false,
          stale_threshold_seconds: null,
          target_date: "2026-03-11",
          latest_cache_date: "2026-03-11",
          stale_reason: "fresh",
        },
        sync_status: {
          service: "cta-sync",
          status: "success",
          trigger: "launchd",
          target_date: "2026-03-11",
          started_at: "2026-03-11T22:05:00Z",
          finished_at: "2026-03-11T22:05:31Z",
          duration_ms: 31_000,
          attempt_count: 1,
          cache_path: "data/menthorq_cache/cta_2026-03-11.json",
          error_type: null,
          error_excerpt: null,
          artifact_log_path: null,
        },
      },
    });

    const { default: CtaPage } = await import("../components/CtaPage");
    const { container } = render(React.createElement(CtaPage));
    const html = container.innerHTML;

    expect(html).not.toContain("CTA CACHE STALE");
    expect(html).toContain("MENTHORQ CTA POSITIONING");
  });
});
