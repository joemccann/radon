/**
 * @vitest-environment jsdom
 *
 * UI regression guards for LlmTokenIndexCard.
 *
 * Behaviours pinned:
 *  - Empty payload renders the empty state, not an error
 *  - Loaded payload renders the chart and the latest index value
 *  - HTTP failure surfaces the error message, not silent blank
 *  - Brand tokens only — no raw hex in the rendered markup
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, cleanup } from "@testing-library/react";

afterEach(() => {
  cleanup();
});

describe("LlmTokenIndexCard", () => {
  let originalFetch: typeof fetch;

  beforeEach(() => {
    originalFetch = global.fetch;
  });

  afterEach(() => {
    global.fetch = originalFetch;
  });

  it("renders the empty state when no rows have been persisted yet", async () => {
    global.fetch = vi.fn(async () =>
      new Response(
        JSON.stringify({ rows: [], count: 0, days: 180 }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    ) as unknown as typeof fetch;

    const { default: LlmTokenIndexCard } = await import("@/components/LlmTokenIndexCard");
    render(<LlmTokenIndexCard />);

    await waitFor(() =>
      expect(screen.getByTestId("llm-token-index-empty")).toBeTruthy(),
    );
    expect(screen.queryByTestId("llm-token-index-chart")).toBeNull();
  });

  it("renders the chart and the latest index value once rows arrive", async () => {
    global.fetch = vi.fn(async () =>
      new Response(
        JSON.stringify({
          rows: [
            { date: "2026-05-15", index_value: 1.0, raw_avg_usd: 15.0, methodology_version: 1 },
            { date: "2026-05-16", index_value: 1.05, raw_avg_usd: 15.75, methodology_version: 1 },
            { date: "2026-05-17", index_value: 1.10, raw_avg_usd: 16.50, methodology_version: 1 },
            { date: "2026-05-18", index_value: 1.15, raw_avg_usd: 17.25, methodology_version: 1 },
            { date: "2026-05-19", index_value: 1.20, raw_avg_usd: 18.00, methodology_version: 1 },
          ],
          count: 5,
          days: 180,
          fetched_at: "2026-05-19T06:30:00Z",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    ) as unknown as typeof fetch;

    const { default: LlmTokenIndexCard } = await import("@/components/LlmTokenIndexCard");
    render(<LlmTokenIndexCard />);

    await waitFor(() =>
      expect(screen.getByTestId("llm-token-index-latest-value")).toBeTruthy(),
    );
    expect(screen.getByTestId("llm-token-index-latest-value").textContent).toBe("1.20");
    expect(screen.getByTestId("llm-token-index-latest-badge").textContent).toBe("1.20");

    // 5d window: (1.20 - 1.00) / 1.00 = +20.0%
    expect(screen.getByTestId("llm-token-index-window-change").textContent).toContain("+20.0%");
    expect(screen.getByTestId("llm-token-index-chart")).toBeTruthy();
    expect(screen.getByRole("img", { name: "Legacy inference price basket, normalized index" })).toBeTruthy();
    expect(screen.getByTestId("llm-token-index-window-change").style.color).toBe("var(--text-secondary)");
    expect(screen.getByTestId("llm-token-index-chart").querySelectorAll("polyline")).toHaveLength(1);
  });

  it("surfaces an error message when the route fails", async () => {
    global.fetch = vi.fn(async () =>
      new Response("nope", { status: 502 }),
    ) as unknown as typeof fetch;

    const { default: LlmTokenIndexCard } = await import("@/components/LlmTokenIndexCard");
    render(<LlmTokenIndexCard />);

    await waitFor(() =>
      expect(screen.getByTestId("llm-token-index-error")).toBeTruthy(),
    );
    expect(screen.getByTestId("llm-token-index-error").textContent).toMatch(/HTTP 502/);
  });

  it("uses brand tokens — no raw hex in the rendered markup", async () => {
    global.fetch = vi.fn(async () =>
      new Response(
        JSON.stringify({
          rows: [
            { date: "2026-05-15", index_value: 1.0, raw_avg_usd: 15.0, methodology_version: 1 },
            { date: "2026-05-19", index_value: 1.20, raw_avg_usd: 18.0, methodology_version: 1 },
          ],
          count: 2,
          days: 180,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    ) as unknown as typeof fetch;

    const { default: LlmTokenIndexCard } = await import("@/components/LlmTokenIndexCard");
    const { container } = render(<LlmTokenIndexCard />);

    await waitFor(() =>
      expect(screen.getByTestId("llm-token-index-chart")).toBeTruthy(),
    );

    // Inline styles set on the card itself should use CSS variables, not raw hex.
    // Walk every element + inspect the style attribute. Allow hex inside SVG
    // children of the stubbed chart by skipping data-testid="stub-chart".
    const elements = container.querySelectorAll<HTMLElement>("[style]");
    for (const el of elements) {
      if (el.closest('[data-testid="stub-chart"]')) continue;
      const style = el.getAttribute("style") ?? "";
      expect(style).not.toMatch(/#[0-9a-fA-F]{3,8}/);
    }
  });
});
