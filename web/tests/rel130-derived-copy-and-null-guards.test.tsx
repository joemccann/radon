/**
 * @vitest-environment jsdom
 */
/**
 * R-357 / R-376 / REL-130: three isolated correctness fixes.
 *
 * R-357: scan-window copy was hardcoded to "5 sessions" in three places while
 * the scan the server runs is `--days 20`, and the same file derived it
 * correctly in the footer. During a scan the operator was told the sample was
 * 5 sessions; when it landed the same screen said 20. Repo rule is derive,
 * never hardcode.
 *
 * R-376: the row-truncation loop introduced by REL-105(a) read
 * `data.tables[key].length` on the ORIGINAL (possibly null) value even though
 * the line above had defensively substituted `[]`, so a cache file carrying
 * `"tables": {"main": null}` threw TypeError and the share-image route 500'd.
 *
 * T-268: this file used to grep three source strings and then EXECUTE a
 * hand-copy of the loop it was testing, so a real edit to the route left the
 * clone (the only thing under test) untouched and every string still present.
 * Both halves now drive the real exports.
 */
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { truncateCtaTables, MAX_IMAGE_ROWS } from "@/lib/ctaImageLayout";
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

describe("R-376 — the real truncation loop, executed", () => {
  it("survives a null table value", () => {
    const tables: Record<string, number[] | null> = { main: null };
    expect(() => truncateCtaTables(tables, 50)).not.toThrow();
    expect(tables.main).toEqual([]);
  });

  it("still truncates past the budget", () => {
    const tables = {
      a: Array.from({ length: 40 }, (_, i) => i),
      b: Array.from({ length: 40 }, (_, i) => i),
    };
    truncateCtaTables(tables, 50);
    expect(tables.a).toHaveLength(40);
    expect(tables.b).toHaveLength(10);
  });

  it("empties, never tail-trims, once the budget is exhausted", () => {
    const tables = { a: [1, 2, 3], b: [1, 2, 3], c: [1, 2, 3] };
    truncateCtaTables(tables, 2);
    expect(tables.a).toHaveLength(2);
    expect(tables.b).toHaveLength(0);
    // Decrementing by `rows.length` rather than `kept.length` drives budget
    // negative, and a negative budget handed to slice() drops rows off the END
    // and keeps the rest, so the plate grows past MAX_IMAGE_ROWS unnoticed.
    expect(tables.c).toHaveLength(0);
  });

  it("caps a whole cache at MAX_IMAGE_ROWS by default", () => {
    const tables = {
      a: Array.from({ length: MAX_IMAGE_ROWS }, (_, i) => i),
      b: Array.from({ length: 100 }, (_, i) => i),
    };
    truncateCtaTables(tables);
    const total = Object.values(tables).reduce((n, rows) => n + rows.length, 0);
    expect(total).toBe(MAX_IMAGE_ROWS);
  });
});

describe("R-376 — the share-image route owns no second copy of the loop", () => {
  /** Wiring only; the behaviour above is asserted against the real export. */
  it("delegates truncation instead of inlining it", () => {
    const src = readFileSync(
      resolve(repo, "web", "app", "api", "menthorq", "cta", "image", "route.tsx"),
      "utf8",
    );
    expect(src.match(/truncateCtaTables\(/g) ?? []).toHaveLength(1);
    expect(src.match(/budget\s*-=/g) ?? []).toHaveLength(0);
  });
});
