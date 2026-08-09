/**
 * @vitest-environment jsdom
 *
 * EngineStatePanel — real regime sources. Pinned behaviours:
 *  - four named rows (CRI / MARKOV / VCG / GEX)
 *  - header count reflects engines with data
 *  - missing payloads render the placeholder, never a fault state
 */
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";

import EngineStatePanel from "@/components/dashboard/EngineStatePanel";
import type { CriData } from "@/lib/useRegime";
import type { VcgData } from "@/lib/useVcg";
import type { GexData } from "@/lib/useGex";

let criData: CriData | null = null;
let vcgData: VcgData | null = null;
let gexData: GexData | null = null;

vi.mock("@/lib/useRegime", () => ({
  useRegime: () => ({ data: criData, loading: false, syncing: false, error: null, lastSync: null, syncNow: vi.fn() }),
}));
vi.mock("@/lib/useVcg", () => ({
  useVcg: () => ({ data: vcgData, loading: false, syncing: false, error: null, lastSync: null, syncNow: vi.fn() }),
}));
vi.mock("@/lib/useGex", () => ({
  useGex: () => ({ data: gexData, loading: false, syncing: false, error: null, lastSync: null, syncNow: vi.fn() }),
}));

afterEach(() => {
  cleanup();
  criData = null;
  vcgData = null;
  gexData = null;
});

function seedAll() {
  criData = {
    cri: { score: 42, level: "ELEVATED", components: { vix: 0, vvix: 0, correlation: 0, momentum: 0 } },
    history: [],
  } as unknown as CriData;
  vcgData = {
    scan_time: "",
    market_open: true,
    credit_proxy: "LQD",
    signal: { interpretation: "NORMAL", regime: "DIVERGENCE", pi_panic: 0.08 },
    history: [],
  } as unknown as VcgData;
  gexData = {
    ticker: "SPX",
    spot: 6400,
    levels: { gex_flip: { strike: 6350, gamma: 0, distance: 50, distance_pct: 0.8 } },
    expected_range: { low: 6300, high: 6500, iv_1d: null },
    bias: { direction: "BULL", reasons: [], days_above_flip: 3, flip_migration: [] },
  } as unknown as GexData;
}

describe("EngineStatePanel", () => {
  it("renders four named engine rows", () => {
    seedAll();
    render(<EngineStatePanel marketState={null} />);
    for (const name of ["CRI", "MARKOV", "VCG", "GEX"]) {
      expect(screen.getByText(name)).toBeTruthy();
    }
    expect(screen.getByText("ELEVATED 42")).toBeTruthy();
    expect(screen.getByText("SPX BULL")).toBeTruthy();
  });

  it("counts engines with data in the header", () => {
    seedAll();
    render(<EngineStatePanel marketState={null} />);
    // CRI history is empty → MARKOV has no data → 3/4
    expect(screen.getByText(/3\/4/)).toBeTruthy();
  });

  it("renders placeholders when payloads are absent", () => {
    render(<EngineStatePanel marketState={null} />);
    expect(screen.getByText(/0\/4/)).toBeTruthy();
    expect(screen.getAllByText("P ---")).toHaveLength(4);
    expect(document.querySelector(".engine-row--fault")).toBeNull();
  });
});
