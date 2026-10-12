// @vitest-environment jsdom

/**
 * CRI input strip: cadence is stated once, not per tile.
 *
 * Market closed, every strip tile used to carry its own DAILY pill and the
 * VIX / VVIX tiles ended in a dead "---" timestamp line. The closed banner
 * already says END OF DAY VALUES, so per-tile DAILY pills are hidden on the
 * closed strip (LIVE pills are untouched) and a missing timestamp renders
 * nothing.
 */

import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: vi.fn(),
    replace: vi.fn(),
    back: vi.fn(),
    forward: vi.fn(),
    refresh: vi.fn(),
    prefetch: vi.fn(),
  }),
  usePathname: () => "/regime/cri",
  useSearchParams: () => new URLSearchParams(),
  redirect: vi.fn(),
}));

// The CRI tab pulls live-sync hooks and a share modal; stub the network edges
// and the heavy D3 history chart internals. The layout under test (the grid,
// its chart slots, and the REAL RegimeRelationshipView) stays rendered.
vi.mock("../lib/useRegime", () => ({
  useRegime: () => ({ data: CRI_DATA, loading: false, syncing: false, lastSync: null }),
}));
vi.mock("../lib/useVcg", () => ({ useVcg: () => ({ data: null }) }));
vi.mock("../lib/useGex", () => ({ useGex: () => ({ data: null }) }));
vi.mock("../lib/useDispersion", () => ({ useDispersion: () => ({ data: null }) }));
vi.mock("../components/ShareReportModal", () => ({ default: () => null }));
vi.mock("../components/CriHistoryChart", () => ({ default: () => null }));

import RegimePanel from "../components/RegimePanel";
import { GLOBALS_CSS_RULES, winningDeclaration } from "./cssCascade";

const HISTORY = Array.from({ length: 20 }, (_, index) => ({
  date: `2026-08-${String(3 + index).padStart(2, "0")}`,
  vix: 15 + index * 0.1,
  vvix: 92 + index * 0.2,
  spy: 640 + index,
  cor1m: 18 + Math.sin(index / 3) * 4,
  realized_vol: 11 + index * 0.15,
  spx_vs_ma_pct: 1.5,
  vix_5d_roc: -2.4,
}));

const CRI_DATA = {
  scan_time: "2026-09-04T16:10:00-04:00",
  market_open: false,
  date: "2026-09-04",
  vix: 16.2,
  vvix: 95.1,
  spy: 655.2,
  vix_5d_roc: -2.4,
  vvix_vix_ratio: 5.87,
  spx_100d_ma: 6300,
  spx_distance_pct: 2.3,
  cor1m: 21.4,
  cor1m_previous_close: 20.9,
  cor1m_5d_change: 1.1,
  realized_vol: 13.2,
  cri: {
    score: 18,
    level: "LOW",
    components: { vix: 4, vvix: 5, correlation: 4, momentum: 5 },
  },
  cta: null,
  menthorq_cta: null,
  crash_trigger: {
    triggered: false,
    conditions: { spx_below_100d_ma: false, realized_vol_gt_25: false, cor1m_gt_60: false },
    values: {},
  },
  history: HISTORY,
};

afterEach(cleanup);

describe("CRI strip cadence (market closed)", () => {
  it("renders no dead --- timestamp line on the VIX and VVIX tiles", () => {
    render(<RegimePanel prices={{}} />);
    for (const id of ["strip-vix", "strip-vvix"]) {
      const cell = screen.getByTestId(id);
      expect(cell.querySelector(".regime-strip-ts")).toBeNull();
      expect(cell.textContent).not.toContain("---");
    }
  });

  it("hides every per-tile DAILY pill on the closed strip", () => {
    render(<RegimePanel prices={{}} />);
    const strip = screen.getByTestId("strip-vix").closest(".regime-strip") as HTMLElement;
    const badges = Array.from(strip.querySelectorAll(".regime-badge"));
    expect(badges.length).toBeGreaterThan(0);
    for (const b of badges) {
      expect(b.getAttribute("data-variant")).toBe("daily");
      expect(winningDeclaration(b, ["display"], GLOBALS_CSS_RULES)?.value).toBe("none");
    }
  });

  it("states end-of-day cadence once in the closed banner", () => {
    render(<RegimePanel prices={{}} />);
    expect(screen.getByTestId("market-closed-indicator").textContent).toMatch(/END OF DAY/);
  });
});
