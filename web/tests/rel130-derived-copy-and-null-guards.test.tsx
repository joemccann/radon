/**
 * @vitest-environment jsdom
 */
/**
 * R-357 / REL-130: derived scan-window copy.
 *
 * R-357: scan-window copy was hardcoded to "5 sessions" in three places while
 * the scan the server runs is `--days 20`, and the same file derived it
 * correctly in the footer. During a scan the operator was told the sample was
 * 5 sessions; when it landed the same screen said 20. Repo rule is derive,
 * never hardcode.
 */
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

import TickerFlowReport, {
  DEFAULT_LOOKBACK_DAYS,
} from "@/components/flow-analysis/TickerFlowReport";

const here = resolve(fileURLToPath(import.meta.url), "..");
const repo = resolve(here, "..", "..");

const flowReport = vi.hoisted(() => ({
  data: null as unknown,
  status: "scanning",
  error: null as string | null,
  refresh: () => undefined,
}));

vi.mock("@/lib/useTickerFlowReport", () => ({
  useTickerFlowReport: () => flowReport,
}));
vi.mock("@/lib/useViewport", () => ({
  useViewport: () => ({
    viewportClass: "desktop",
    isMobile: false,
    isTablet: false,
    isDesktop: true,
    width: 1440,
    hasMounted: true,
  }),
}));

afterEach(cleanup);

describe("R-357 — the scan window is derived, never hardcoded", () => {
  it("renders every window string from the one declared default", () => {
    render(<TickerFlowReport ticker="AAPL" />);
    const text = document.body.textContent ?? "";

    // Rendered, not grepped: `lookbackDays={5}` on the panel leaves every
    // source string of the old assertions intact and still tells the operator
    // a sample size the server never scanned.
    expect(text).toContain(`Sampling AAPL flow · ${DEFAULT_LOOKBACK_DAYS} sessions`);
    expect(text).toContain(
      `Pulling dark pool prints across the last ${DEFAULT_LOOKBACK_DAYS} trading sessions`,
    );
  });

  it("matches the --days the server actually passes to flow_report.py", () => {
    const server = readFileSync(resolve(repo, "scripts", "api", "server.py"), "utf8");
    const match = server.match(/"--days",\s*"(\d+)"/);
    expect(match, "server.py no longer passes a literal --days to flow_report.py").toBeTruthy();
    expect(Number(match![1])).toBe(DEFAULT_LOOKBACK_DAYS);
  });
});
