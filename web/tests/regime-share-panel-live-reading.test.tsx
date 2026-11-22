/** @vitest-environment jsdom */
/**
 * RegimePanel Share to X posts the live CRI the hero is showing.
 * 2026-11-19: the page showed CRI 51 from live ticks; the cards showed 48
 * because the owner never handed the reading to the modal.
 */
import React from "react";
import {cleanup, fireEvent, render, screen, waitFor} from "@testing-library/react";
import {afterEach, beforeEach, describe, expect, it, vi} from "vitest";
import {computeCri} from "../lib/criCalc";

(globalThis as {IS_REACT_ACT_ENVIRONMENT?: boolean}).IS_REACT_ACT_ENVIRONMENT = true;

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

vi.mock("../lib/useRegime", () => ({
  useRegime: () => ({data: CRI_DATA, loading: false, syncing: false, lastSync: null}),
}));
vi.mock("../lib/useVcg", () => ({useVcg: () => ({data: null})}));
vi.mock("../lib/useGex", () => ({useGex: () => ({data: null})}));
vi.mock("../lib/useDispersion", () => ({useDispersion: () => ({data: null})}));
vi.mock("../components/CriHistoryChart", () => ({default: () => null}));
vi.mock("../components/RegimeRelationshipView", () => ({default: () => null}));
vi.mock("@/lib/useDialogChrome", () => ({useDialogChrome: () => ({panelRef: {current: null}})}));

import RegimePanel from "../components/RegimePanel";

const CRI_DATA = {
  scan_time: "2026-11-19T17:00:00Z",
  market_open: true,
  date: "2026-11-19",
  vix: 27.5,
  vvix: 125.1,
  spy: 718.0,
  vix_5d_roc: 40.0,
  vvix_vix_ratio: 4.5,
  spx_100d_ma: 759.97,
  spx_distance_pct: -5.4,
  cor1m: 23.2,
  cor1m_previous_close: 20.9,
  cor1m_5d_change: 5.0,
  realized_vol: 23.0,
  cri: {
    score: 48,
    level: "ELEVATED",
    components: {vix: 16.2, vvix: 12.5, correlation: 5.2, momentum: 14.0},
  },
  crash_trigger: {
    triggered: false,
    conditions: {spx_below_100d_ma: true, realized_vol_gt_25: false, cor1m_gt_60: false},
  },
  history: [],
};

const LIVE_PRICES = {
  VIX: {last: 28.54},
  VVIX: {last: 127.56},
  SPY: {last: 716.52},
};

const liveCri = computeCri({
  vix: 28.54,
  vix5dRoc: 40.0,
  vvix: 127.56,
  vvixVixRatio: 127.56 / 28.54,
  corr: 23.2,
  corr5dChange: 5.0,
  spxDistancePct: ((716.52 / 759.97) - 1) * 100,
});

function stubFetch() {
  const fetch = vi.fn().mockResolvedValue(new Response("{}", {status: 500}));
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

describe("RegimePanel live share reading", () => {
  beforeEach(() => {
    vi.stubGlobal("URL", Object.assign(URL, {
      createObjectURL: vi.fn(() => "blob:mock"),
      revokeObjectURL: vi.fn(),
    }));
  });

  afterEach(async () => {
    cleanup();
    await new Promise((resolve) => setImmediate(resolve));
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("posts the live CRI the hero shows, not the scan snapshot", async () => {
    expect(liveCri.score).not.toBe(48);
    const fetch = stubFetch();
    render(<RegimePanel prices={LIVE_PRICES as never} />);
    expect(fetch).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", {name: "Share to X"}));
    await waitFor(() => expect(fetch).toHaveBeenCalled());
    expect(fetch).toHaveBeenCalledExactlyOnceWith("/api/regime/share", {
      method: "POST",
      signal: expect.any(AbortSignal),
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        live: {
          vix: 28.54,
          vvix: 127.56,
          spy: 716.52,
          cor1m: 23.2,
          cor1m_5d_change: 5.0,
          vvix_vix_ratio: 127.56 / 28.54,
          spx_distance_pct: ((716.52 / 759.97) - 1) * 100,
          realized_vol: 23.0,
          cri: liveCri,
          crash_trigger: {conditions: {spx_below_100d_ma: true, cor1m_gt_60: false}},
        },
      }),
    });
  });

  it("posts no body when the page is showing the scan snapshot", async () => {
    const fetch = stubFetch();
    render(<RegimePanel prices={{}} />);
    fireEvent.click(screen.getByRole("button", {name: "Share to X"}));
    await waitFor(() => expect(fetch).toHaveBeenCalled());
    expect(fetch).toHaveBeenCalledExactlyOnceWith("/api/regime/share", {
      method: "POST",
      signal: expect.any(AbortSignal),
    });
  });
});
