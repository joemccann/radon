/**
 * @vitest-environment jsdom
 *
 * Tests for <ServiceHealthBanner /> — banner is HIDDEN in steady state
 * (failing.length === 0) and renders with the failing service names + the
 * first failing row's last_error when degraded.
 */
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, cleanup, screen } from "@testing-library/react";

afterEach(() => {
  cleanup();
  vi.resetModules();
});

type FailingFixture = {
  service: string;
  state: string;
  category?: "scheduled" | "on-demand";
  last_error: string | null;
  error_summary?: string | null;
};

function mockUseServiceHealth(payload: {
  failing?: FailingFixture[];
  services?: FailingFixture[];
  degraded_count?: number;
  dormant_count?: number;
} | null): void {
  vi.doMock("@/lib/useServiceHealth", () => ({
    useServiceHealth: () => ({
      data: payload === null
        ? null
        : {
          services: payload.services ?? payload.failing ?? [],
          failing: payload.failing ?? [],
          degraded_count:
            payload.degraded_count ?? (payload.failing ?? []).filter(
              (r) =>
                r.state === "error" ||
                (r.state === "stale" && r.category !== "on-demand"),
            ).length,
          dormant_count:
            payload.dormant_count ??
            (payload.services ?? []).filter((r) => r.state === "dormant").length,
          summary: {
            total: (payload.failing ?? []).length,
            failing_count: (payload.failing ?? []).length,
          },
        },
      loading: false,
    }),
  }));
}

describe("<ServiceHealthBanner />", () => {
  it("renders nothing when data is null (initial fetch)", async () => {
    mockUseServiceHealth(null);
    const { default: Banner } = await import("../components/ServiceHealthBanner");
    const { container } = render(<Banner />);
    expect(container.firstChild).toBeNull();
  });

  it("renders nothing when no failing services (steady state)", async () => {
    mockUseServiceHealth({ failing: [] });
    const { default: Banner } = await import("../components/ServiceHealthBanner");
    const { container } = render(<Banner />);
    expect(container.firstChild).toBeNull();
  });

  it("renders banner when one service is failing", async () => {
    mockUseServiceHealth({
      failing: [{ service: "portfolio-sync", state: "error", last_error: "WAL locked" }],
    });
    const { default: Banner } = await import("../components/ServiceHealthBanner");
    render(<Banner />);
    const banner = screen.getByTestId("service-health-banner");
    expect(banner.textContent).toContain("Background sync degraded");
    expect(banner.textContent).toContain("portfolio-sync");
    // Banner now humanizes "WAL locked" into "Database temporarily busy".
    expect(banner.textContent).toContain("Database temporarily busy");
    expect(banner.textContent).not.toContain("WAL");
    expect(
      banner.querySelector(".service-health-banner__message")?.getAttribute("title"),
    ).toBe("Background sync degraded: portfolio-sync - Database temporarily busy");
  });

  it("lists multiple failing services up to 3 + truncates rest with +N more", async () => {
    mockUseServiceHealth({
      failing: [
        { service: "a", state: "error", last_error: null },
        { service: "b", state: "error", last_error: null },
        { service: "c", state: "error", last_error: null },
        { service: "d", state: "error", last_error: null },
        { service: "e", state: "error", last_error: null },
      ],
    });
    const { default: Banner } = await import("../components/ServiceHealthBanner");
    render(<Banner />);
    const banner = screen.getByTestId("service-health-banner");
    expect(banner.textContent).toContain("a, b, c");
    expect(banner.textContent).toContain("+2 more");
    expect(banner.textContent).not.toContain(", d");
  });

  it("doesn't render last_error when first failing row has null error", async () => {
    mockUseServiceHealth({
      failing: [{ service: "scanner", state: "error", last_error: null }],
    });
    const { default: Banner } = await import("../components/ServiceHealthBanner");
    render(<Banner />);
    const banner = screen.getByTestId("service-health-banner");
    expect(banner.textContent).toContain("scanner");
    expect(banner.querySelector(".service-health-banner__detail")).toBeNull();
  });

  // Regression: the production banner was rendering the raw JSON payload
  // shipped by the route — ``{"message": "ERR: ..."}`` — leaking braces
  // and quotes into user-visible copy and mid-cutting on overflow. The
  // banner now leans on the route's ``error_summary`` and re-runs the
  // same formatter defensively when only ``last_error`` is present.
  describe("JSON-encoded last_error payloads (regression: braces/quotes leak)", () => {
    it("renders the humanized message when given a JSON-stringified Flex throttle via last_error", async () => {
      const raw = JSON.stringify({
        message:
          "ERR: cash flow fetch failed: Flex SendRequest failed (code 1001): Statement could not be generated at this time. Please try again shortly.",
      });
      mockUseServiceHealth({
        failing: [
          { service: "cash-flow-sync", state: "error", last_error: raw },
        ],
      });
      const { default: Banner } = await import("../components/ServiceHealthBanner");
      render(<Banner />);
      const banner = screen.getByTestId("service-health-banner");
      const text = banner.textContent ?? "";
      expect(text).toContain("cash-flow-sync");
      // The banner now rewrites the developer-flavoured Flex error into
      // operator-friendly copy. Old verbatim wording must NOT leak.
      expect(text).toContain("Flex Web Service rate limit hit");
      expect(text).not.toContain("ERR:");
      expect(text).not.toContain("SendRequest");
      expect(text).not.toContain("(code 1001)");
      // No JSON structural characters should leak through.
      expect(text).not.toContain("{");
      expect(text).not.toContain("}");
      expect(text).not.toContain('"');
    });

    it("falls back to error_summary when last_error is null but route shipped a summary", async () => {
      mockUseServiceHealth({
        failing: [
          {
            service: "cash-flow-sync",
            state: "error",
            last_error: null,
            // Already-cleaned plain text from the route.
            error_summary: "Flex Web Service was unreachable",
          },
        ],
      });
      const { default: Banner } = await import("../components/ServiceHealthBanner");
      render(<Banner />);
      const banner = screen.getByTestId("service-health-banner");
      expect(banner.textContent).toContain("Flex Web Service was unreachable");
      expect(banner.textContent).not.toContain('"');
    });

    it("prefers raw last_error over error_summary when both are present", async () => {
      // The raw payload carries metadata (next_attempt_at, structured
      // error codes) that the API's pre-normaliser strips. The banner
      // should humanize the raw payload directly so we keep that signal.
      mockUseServiceHealth({
        failing: [
          {
            service: "cash-flow-sync",
            state: "error",
            last_error: JSON.stringify({
              message: "Flex SendRequest failed (code 1001): ...",
            }),
            error_summary: "Some lossy summary the route produced",
          },
        ],
      });
      const { default: Banner } = await import("../components/ServiceHealthBanner");
      render(<Banner />);
      const banner = screen.getByTestId("service-health-banner");
      expect(banner.textContent).toContain("Flex Web Service rate limit hit");
      expect(banner.textContent).not.toContain("Some lossy summary");
    });

    it("humanizes a plain non-JSON WAL string into Database temporarily busy", async () => {
      mockUseServiceHealth({
        failing: [{ service: "cri-scan", state: "error", last_error: "WAL locked" }],
      });
      const { default: Banner } = await import("../components/ServiceHealthBanner");
      render(<Banner />);
      const banner = screen.getByTestId("service-health-banner");
      expect(banner.textContent).toContain("Database temporarily busy");
      expect(banner.textContent).not.toContain("WAL");
      expect(banner.textContent).not.toContain("{");
      expect(banner.textContent).not.toContain('"');
    });

    it("passes through a novel plain string we don't recognise", async () => {
      mockUseServiceHealth({
        failing: [
          { service: "cri-scan", state: "error", last_error: "something we never saw before" },
        ],
      });
      const { default: Banner } = await import("../components/ServiceHealthBanner");
      render(<Banner />);
      const banner = screen.getByTestId("service-health-banner");
      expect(banner.textContent).toContain("something we never saw before");
    });

    it("appends a relative retry window when last_error carries next_attempt_at", async () => {
      const future = new Date(Date.now() + 24 * 60 * 60 * 1000).toISOString();
      mockUseServiceHealth({
        failing: [
          {
            service: "cash-flow-sync",
            state: "error",
            last_error: JSON.stringify({
              message: "Flex SendRequest failed (code 1001): ...",
              next_attempt_at: future,
            }),
          },
        ],
      });
      const { default: Banner } = await import("../components/ServiceHealthBanner");
      render(<Banner />);
      const banner = screen.getByTestId("service-health-banner");
      const text = banner.textContent ?? "";
      expect(text).toContain("Flex Web Service rate limit hit");
      expect(text.toLowerCase()).toContain("retry in");
      // Retry window expressed in compact units, never raw ISO.
      expect(text).not.toContain("T");
      expect(text).not.toContain("Z");
    });

    it("uses the `error` key when the JSON payload omits `message`", async () => {
      const raw = JSON.stringify({ error: "connection refused", wal_conflicts_observed: 4 });
      mockUseServiceHealth({
        failing: [{ service: "portfolio-sync", state: "error", last_error: raw }],
      });
      const { default: Banner } = await import("../components/ServiceHealthBanner");
      render(<Banner />);
      const banner = screen.getByTestId("service-health-banner");
      expect(banner.textContent).toContain("connection refused");
      expect(banner.textContent).not.toContain("wal_conflicts_observed");
      expect(banner.textContent).not.toContain("{");
    });

    it("falls back to generic copy when the JSON payload has no recognisable message keys", async () => {
      const raw = JSON.stringify({ wal_conflicts_observed: 7, retry_count: 3 });
      mockUseServiceHealth({
        failing: [{ service: "newsfeed-scraper", state: "error", last_error: raw }],
      });
      const { default: Banner } = await import("../components/ServiceHealthBanner");
      render(<Banner />);
      const banner = screen.getByTestId("service-health-banner");
      expect(banner.textContent).toContain("service unavailable");
      expect(banner.textContent).not.toContain("{");
      expect(banner.textContent).not.toContain("wal_conflicts_observed");
    });

    it("renders the humanized Flex throttle copy in well under banner width", async () => {
      const longMessage =
        "ERR: cash flow fetch failed: Flex SendRequest failed (code 1001): Statement could not be generated at this time. Please try again shortly.";
      mockUseServiceHealth({
        failing: [
          {
            service: "cash-flow-sync",
            state: "error",
            last_error: JSON.stringify({ message: longMessage }),
          },
        ],
      });
      const { default: Banner } = await import("../components/ServiceHealthBanner");
      render(<Banner />);
      const detail = screen
        .getByTestId("service-health-banner")
        .querySelector(".service-health-banner__detail");
      expect(detail).not.toBeNull();
      const detailText = detail?.textContent ?? "";
      // The humanized copy is much shorter than the raw payload, and
      // ends as a complete phrase rather than mid-sentence.
      expect(detailText.length).toBeLessThan(longMessage.length);
      expect(detailText).toContain("Flex Web Service rate limit hit");
      // No mid-word truncation marker on the rewritten copy.
      expect(detailText).not.toContain("...");
    });
  });

  /**
   * Dormant on-demand services are NOT surfaced (2026-05-14). They were
   * informational noise that nobody could act on — "scanner has been
   * dormant for 5 days" just means the user hasn't visited the page.
   * Showing the chip constantly conditioned the user to ignore the
   * banner, masking real failures. The banner now hides whenever there
   * are zero degraded rows, regardless of dormant_count.
   */
  describe("on-demand dormant chip removed", () => {
    it("does NOT show the banner when only dormant services exist", async () => {
      mockUseServiceHealth({
        failing: [],
        services: [
          { service: "scanner", state: "dormant", category: "on-demand", last_error: null },
          { service: "discover", state: "dormant", category: "on-demand", last_error: null },
          { service: "gex-scan", state: "dormant", category: "on-demand", last_error: null },
        ],
        degraded_count: 0,
        dormant_count: 3,
      });
      const { default: Banner } = await import("../components/ServiceHealthBanner");
      const { container } = render(<Banner />);
      expect(container.firstChild).toBeNull();
    });

    it("renders ONLY the degraded message when both degraded and dormant rows exist", async () => {
      mockUseServiceHealth({
        failing: [
          { service: "newsfeed-scraper", state: "error", category: "scheduled", last_error: "boom" },
        ],
        services: [
          { service: "newsfeed-scraper", state: "error", category: "scheduled", last_error: "boom" },
          { service: "scanner", state: "dormant", category: "on-demand", last_error: null },
          { service: "discover", state: "dormant", category: "on-demand", last_error: null },
        ],
        degraded_count: 1,
        dormant_count: 2,
      });
      const { default: Banner } = await import("../components/ServiceHealthBanner");
      render(<Banner />);
      const banner = screen.getByTestId("service-health-banner");
      // Degraded copy is rendered.
      expect(banner.textContent).toContain("newsfeed-scraper");
      expect(banner.textContent).toContain("Background sync degraded");
      // Dormant chip element is gone entirely.
      expect(banner.querySelector(".service-health-banner__dormant")).toBeNull();
      expect(banner.textContent).not.toContain("scanner");
      expect(banner.textContent).not.toContain("discover");
      expect(banner.textContent?.toLowerCase()).not.toContain("visit to refresh");
    });

    it("does NOT include the on-demand row name in the degraded list", async () => {
      mockUseServiceHealth({
        failing: [
          { service: "newsfeed-scraper", state: "error", category: "scheduled", last_error: "boom" },
        ],
        services: [
          { service: "newsfeed-scraper", state: "error", category: "scheduled", last_error: "boom" },
          { service: "scanner", state: "dormant", category: "on-demand", last_error: null },
        ],
        degraded_count: 1,
        dormant_count: 1,
      });
      const { default: Banner } = await import("../components/ServiceHealthBanner");
      render(<Banner />);
      const banner = screen.getByTestId("service-health-banner");
      const headline = banner.querySelector(".service-health-banner__message");
      expect(headline?.textContent).toContain("newsfeed-scraper");
      expect(headline?.textContent).not.toContain("scanner");
    });
  });
});
