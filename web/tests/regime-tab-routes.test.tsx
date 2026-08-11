/**
 * @vitest-environment jsdom
 *
 * Each regime tab is its own route:
 *   /regime/cri  /regime/vcg  /regime/gex  /regime/grg
 * The bare /regime route redirects to /regime/cri, the sidebar nav points at
 * /regime/cri, and clicking a tab inside RegimePanel pushes the matching URL
 * (state lives in the URL, not in component state).
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { readFileSync, existsSync } from "fs";
import { join } from "path";
import React from "react";
import { render, cleanup, within } from "@testing-library/react";

const ROOT = join(__dirname, "..");
const read = (rel: string) => readFileSync(join(ROOT, rel), "utf-8");

/* ─── 1. Sidebar / nav ─────────────────────────────────── */

describe("navItems — regime href targets the cri tab", () => {
  it("regime entry links to /regime/cri so users land on a real subroute", async () => {
    const { navItems } = await import("../lib/data");
    const regime = navItems.find((n) => n.route === "regime");
    expect(regime).toBeDefined();
    expect(regime!.href).toBe("/regime/cri");
  });
});

/* ─── 2. /regime root → /regime/cri redirect ───────────── */

describe("app/regime/page.tsx — bare /regime redirects to /regime/cri", () => {
  const src = read("app/regime/page.tsx");

  it("imports redirect from next/navigation", () => {
    expect(src).toMatch(/from\s+["']next\/navigation["']/);
    expect(src).toMatch(/redirect/);
  });

  it("redirects to /regime/cri", () => {
    expect(src).toMatch(/redirect\(["']\/regime\/cri["']\)/);
  });
});

/* ─── 3. Subroute pages render WorkspaceShell ──────────── */

describe.each([
  ["cri", "app/regime/cri/page.tsx"],
  ["vcg", "app/regime/vcg/page.tsx"],
  ["gex", "app/regime/gex/page.tsx"],
  ["grg", "app/regime/grg/page.tsx"],
  ["breadth", "app/regime/breadth/page.tsx"],
  ["bpi", "app/regime/bpi/page.tsx"],
  ["margin", "app/regime/margin/page.tsx"],
  ["straddle", "app/regime/straddle/page.tsx"],
  ["cor", "app/regime/cor/page.tsx"],
  ["skew", "app/regime/skew/page.tsx"],
  ["skew2d", "app/regime/skew2d/page.tsx"],
  ["curve", "app/regime/curve/page.tsx"],
  ["cot", "app/regime/cot/page.tsx"],
  ["ats", "app/regime/ats/page.tsx"],
  ["short", "app/regime/short/page.tsx"],
])("app/regime/%s/page.tsx exists and mounts WorkspaceShell", (_tab, rel) => {
  it(`file ${rel} exists`, () => {
    expect(existsSync(join(ROOT, rel))).toBe(true);
  });

  it("renders WorkspaceShell with section='regime'", () => {
    const src = read(rel);
    expect(src).toMatch(/import\s+WorkspaceShell/);
    expect(src).toMatch(/section=["']regime["']/);
  });
});

/* ─── 4. RegimePanel reads tab from URL + pushes on click ─ */

const pushSpy = vi.fn();
let mockedPathname = "/regime/cri";
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushSpy, replace: vi.fn(), back: vi.fn(), forward: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => mockedPathname,
  useSearchParams: () => new URLSearchParams(),
  redirect: vi.fn(),
}));

// Heavy descendants render real DOM trees and pull in network/WS clients —
// stub them. We only care that RegimePanel selects the right child.
vi.mock("../components/VcgPanel", () => ({
  default: () => <div data-testid="vcg-panel-stub" />,
}));
vi.mock("../components/GexPanel", () => ({
  default: () => <div data-testid="gex-panel-stub" />,
}));
vi.mock("../components/GammaRotationPanel", () => ({
  default: () => <div data-testid="grg-panel-stub" />,
}));
vi.mock("../components/BreadthPanel", () => ({
  default: () => <div data-testid="breadth-panel-stub" />,
}));
vi.mock("../components/BpiPanel", () => ({
  default: () => <div data-testid="bpi-panel-stub" />,
}));
vi.mock("../components/MarginDebtPanel", () => ({
  default: () => <div data-testid="margin-panel-stub" />,
}));
vi.mock("../components/StraddlePanel", () => ({
  default: () => <div data-testid="straddle-panel-stub" />,
}));
vi.mock("../components/CorPanel", () => ({
  default: () => <div data-testid="cor-panel-stub" />,
}));
vi.mock("../components/SkewPanel", () => ({
  default: () => <div data-testid="skew-panel-stub" />,
}));
vi.mock("../components/Skew2dPanel", () => ({
  default: () => <div data-testid="skew2d-panel-stub" />,
}));
vi.mock("../components/YieldCurvePanel", () => ({
  default: () => <div data-testid="curve-panel-stub" />,
}));
vi.mock("../components/equibles-cot/EquiblesCotPanel", () => ({
  default: () => <div data-testid="cot-panel-stub" />,
}));
vi.mock("../components/equibles-ats-venue-share/AtsVenueSharePanel", () => ({
  default: () => <div data-testid="ats-panel-stub" />,
}));
vi.mock("../components/equibles/EquiblesShortCrowdingPanel", () => ({
  default: () => <div data-testid="short-panel-stub" />,
}));
vi.mock("../components/CriHistoryChart", () => ({ default: () => null }));
vi.mock("../components/RegimeRelationshipView", () => ({ default: () => null }));
vi.mock("../components/ShareReportModal", () => ({ default: () => null }));
vi.mock("../lib/useRegime", () => ({
  useRegime: () => ({ data: null, syncing: false, lastSync: null, error: null, syncNow: vi.fn() }),
}));

import RegimePanel from "../components/RegimePanel";

describe("RegimePanel — tab is URL-driven", () => {
  beforeEach(() => {
    pushSpy.mockClear();
  });
  afterEach(() => {
    cleanup();
  });

  it("renders the CRI view when pathname is /regime/cri", () => {
    mockedPathname = "/regime/cri";
    const { container } = render(<RegimePanel prices={{}} />);
    expect(within(container).queryByTestId("vcg-panel-stub")).toBeNull();
    expect(within(container).queryByTestId("gex-panel-stub")).toBeNull();
    expect(within(container).queryByTestId("grg-panel-stub")).toBeNull();
  });

  it("renders the VCG panel when pathname is /regime/vcg", () => {
    mockedPathname = "/regime/vcg";
    const { container } = render(<RegimePanel prices={{}} />);
    expect(within(container).getByTestId("vcg-panel-stub")).toBeTruthy();
    expect(within(container).queryByTestId("gex-panel-stub")).toBeNull();
    expect(within(container).queryByTestId("grg-panel-stub")).toBeNull();
  });

  it("renders the GEX panel when pathname is /regime/gex", () => {
    mockedPathname = "/regime/gex";
    const { container } = render(<RegimePanel prices={{}} />);
    expect(within(container).getByTestId("gex-panel-stub")).toBeTruthy();
    expect(within(container).queryByTestId("vcg-panel-stub")).toBeNull();
    expect(within(container).queryByTestId("grg-panel-stub")).toBeNull();
  });

  it("renders the GRG panel when pathname is /regime/grg", () => {
    mockedPathname = "/regime/grg";
    const { container } = render(<RegimePanel prices={{}} />);
    expect(within(container).getByTestId("grg-panel-stub")).toBeTruthy();
    expect(within(container).queryByTestId("vcg-panel-stub")).toBeNull();
    expect(within(container).queryByTestId("gex-panel-stub")).toBeNull();
  });

  it("falls back to CRI for an unknown subpath", () => {
    mockedPathname = "/regime/bogus";
    const { container } = render(<RegimePanel prices={{}} />);
    expect(within(container).queryByTestId("vcg-panel-stub")).toBeNull();
    expect(within(container).queryByTestId("gex-panel-stub")).toBeNull();
    expect(within(container).queryByTestId("grg-panel-stub")).toBeNull();
  });

  it("clicking VCG tab pushes /regime/vcg", () => {
    mockedPathname = "/regime/cri";
    const { container } = render(<RegimePanel prices={{}} />);
    within(container).getByRole("button", { name: /^VCG$/ }).click();
    expect(pushSpy).toHaveBeenCalledWith("/regime/vcg");
  });

  it("clicking GEX tab pushes /regime/gex", () => {
    mockedPathname = "/regime/cri";
    const { container } = render(<RegimePanel prices={{}} />);
    within(container).getByRole("button", { name: /^GEX$/ }).click();
    expect(pushSpy).toHaveBeenCalledWith("/regime/gex");
  });

  it("clicking GRG tab pushes /regime/grg", () => {
    mockedPathname = "/regime/cri";
    const { container } = render(<RegimePanel prices={{}} />);
    within(container).getByRole("button", { name: /^GRG$/ }).click();
    expect(pushSpy).toHaveBeenCalledWith("/regime/grg");
  });

  it("renders the Breadth panel when pathname is /regime/breadth", () => {
    mockedPathname = "/regime/breadth";
    const { container } = render(<RegimePanel prices={{}} />);
    expect(within(container).getByTestId("breadth-panel-stub")).toBeTruthy();
    expect(within(container).queryByTestId("vcg-panel-stub")).toBeNull();
  });

  it("clicking BREADTH tab pushes /regime/breadth", () => {
    mockedPathname = "/regime/cri";
    const { container } = render(<RegimePanel prices={{}} />);
    within(container).getByRole("button", { name: /^BREADTH$/ }).click();
    expect(pushSpy).toHaveBeenCalledWith("/regime/breadth");
  });

  it("renders the BPI panel when pathname is /regime/bpi", () => {
    mockedPathname = "/regime/bpi";
    const { container } = render(<RegimePanel prices={{}} />);
    expect(within(container).getByTestId("bpi-panel-stub")).toBeTruthy();
    expect(within(container).queryByTestId("breadth-panel-stub")).toBeNull();
  });

  it("clicking BULLISH % tab pushes /regime/bpi", () => {
    mockedPathname = "/regime/cri";
    const { container } = render(<RegimePanel prices={{}} />);
    within(container).getByRole("button", { name: /^BULLISH %$/ }).click();
    expect(pushSpy).toHaveBeenCalledWith("/regime/bpi");
  });

  it("renders the Margin panel when pathname is /regime/margin", () => {
    mockedPathname = "/regime/margin";
    const { container } = render(<RegimePanel prices={{}} />);
    expect(within(container).getByTestId("margin-panel-stub")).toBeTruthy();
    expect(within(container).queryByTestId("breadth-panel-stub")).toBeNull();
  });

  it("clicking MARGIN tab pushes /regime/margin", () => {
    mockedPathname = "/regime/cri";
    const { container } = render(<RegimePanel prices={{}} />);
    within(container).getByRole("button", { name: /^MARGIN$/ }).click();
    expect(pushSpy).toHaveBeenCalledWith("/regime/margin");
  });

  it("renders the Straddle panel when pathname is /regime/straddle", () => {
    mockedPathname = "/regime/straddle";
    const { container } = render(<RegimePanel prices={{}} />);
    expect(within(container).getByTestId("straddle-panel-stub")).toBeTruthy();
    expect(within(container).queryByTestId("margin-panel-stub")).toBeNull();
  });

  it("clicking STRADDLE tab pushes /regime/straddle", () => {
    mockedPathname = "/regime/cri";
    const { container } = render(<RegimePanel prices={{}} />);
    within(container).getByRole("button", { name: /^STRADDLE$/ }).click();
    expect(pushSpy).toHaveBeenCalledWith("/regime/straddle");
  });

  it("renders the Cor panel when pathname is /regime/cor", () => {
    mockedPathname = "/regime/cor";
    const { container } = render(<RegimePanel prices={{}} />);
    expect(within(container).getByTestId("cor-panel-stub")).toBeTruthy();
    expect(within(container).queryByTestId("straddle-panel-stub")).toBeNull();
  });

  it("clicking COR tab pushes /regime/cor", () => {
    mockedPathname = "/regime/cri";
    const { container } = render(<RegimePanel prices={{}} />);
    within(container).getByRole("button", { name: /^COR$/ }).click();
    expect(pushSpy).toHaveBeenCalledWith("/regime/cor");
  });

  it("renders the Skew panel when pathname is /regime/skew", () => {
    mockedPathname = "/regime/skew";
    const { container } = render(<RegimePanel prices={{}} />);
    expect(within(container).getByTestId("skew-panel-stub")).toBeTruthy();
    expect(within(container).queryByTestId("straddle-panel-stub")).toBeNull();
  });

  it("clicking SKEW tab pushes /regime/skew", () => {
    mockedPathname = "/regime/cri";
    const { container } = render(<RegimePanel prices={{}} />);
    within(container).getByRole("button", { name: /^SKEW$/ }).click();
    expect(pushSpy).toHaveBeenCalledWith("/regime/skew");
  });

  it("renders the Skew2d panel when pathname is /regime/skew2d", () => {
    mockedPathname = "/regime/skew2d";
    const { container } = render(<RegimePanel prices={{}} />);
    expect(within(container).getByTestId("skew2d-panel-stub")).toBeTruthy();
    expect(within(container).queryByTestId("skew-panel-stub")).toBeNull();
  });

  it("clicking SKEW 2D tab pushes /regime/skew2d", () => {
    mockedPathname = "/regime/cri";
    const { container } = render(<RegimePanel prices={{}} />);
    within(container).getByRole("button", { name: /^SKEW 2D$/ }).click();
    expect(pushSpy).toHaveBeenCalledWith("/regime/skew2d");
  });

  it("renders the Yield Curve panel when pathname is /regime/curve", () => {
    mockedPathname = "/regime/curve";
    const { container } = render(<RegimePanel prices={{}} />);
    expect(within(container).getByTestId("curve-panel-stub")).toBeTruthy();
    expect(within(container).queryByTestId("margin-panel-stub")).toBeNull();
  });

  it("clicking CURVE tab pushes /regime/curve", () => {
    mockedPathname = "/regime/cri";
    const { container } = render(<RegimePanel prices={{}} />);
    within(container).getByRole("button", { name: /^CURVE$/ }).click();
    expect(pushSpy).toHaveBeenCalledWith("/regime/curve");
  });

  it("renders the COT panel when pathname is /regime/cot", () => {
    mockedPathname = "/regime/cot";
    const { container } = render(<RegimePanel prices={{}} />);
    expect(within(container).getByTestId("cot-panel-stub")).toBeTruthy();
    expect(within(container).queryByTestId("curve-panel-stub")).toBeNull();
  });

  it("clicking COT tab pushes /regime/cot", () => {
    mockedPathname = "/regime/cri";
    const { container } = render(<RegimePanel prices={{}} />);
    within(container).getByRole("button", { name: /^COT$/ }).click();
    expect(pushSpy).toHaveBeenCalledWith("/regime/cot");
  });

  it("renders the ATS venue-share panel when pathname is /regime/ats", () => {
    mockedPathname = "/regime/ats";
    const { container } = render(<RegimePanel prices={{}} />);
    expect(within(container).getByTestId("ats-panel-stub")).toBeTruthy();
    expect(within(container).queryByTestId("cot-panel-stub")).toBeNull();
  });

  it("clicking ATS tab pushes /regime/ats", () => {
    mockedPathname = "/regime/cri";
    const { container } = render(<RegimePanel prices={{}} />);
    within(container).getByRole("button", { name: /^ATS$/ }).click();
    expect(pushSpy).toHaveBeenCalledWith("/regime/ats");
  });

  it("renders the short-crowding panel when pathname is /regime/short", () => {
    mockedPathname = "/regime/short";
    const { container } = render(<RegimePanel prices={{}} />);
    expect(within(container).getByTestId("short-panel-stub")).toBeTruthy();
    expect(within(container).queryByTestId("ats-panel-stub")).toBeNull();
  });

  it("clicking SHORT tab pushes /regime/short", () => {
    mockedPathname = "/regime/cri";
    const { container } = render(<RegimePanel prices={{}} />);
    within(container).getByRole("button", { name: /^SHORT$/ }).click();
    expect(pushSpy).toHaveBeenCalledWith("/regime/short");
  });

  it("clicking CRI from VCG pushes /regime/cri", () => {
    mockedPathname = "/regime/vcg";
    const { container } = render(<RegimePanel prices={{}} />);
    within(container).getByRole("button", { name: /^CRI$/ }).click();
    expect(pushSpy).toHaveBeenCalledWith("/regime/cri");
  });
});

/* ─── 5. RegimePanel no longer owns tab state ──────────── */

describe("RegimePanel source — no internal useState for active tab", () => {
  const src = read("components/RegimePanel.tsx");

  it("does not declare a setActiveTab useState", () => {
    expect(src).not.toMatch(/setActiveTab\s*\]\s*=\s*useState/);
  });

  it("imports usePathname and useRouter from next/navigation", () => {
    expect(src).toMatch(/from\s+["']next\/navigation["']/);
    expect(src).toMatch(/usePathname/);
    expect(src).toMatch(/useRouter/);
  });
});
