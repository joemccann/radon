/**
 * @vitest-environment jsdom
 *
 * Each regime tab is its own route:
 *   /regime/cri  /regime/vcg  /regime/gex
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
  });

  it("renders the VCG panel when pathname is /regime/vcg", () => {
    mockedPathname = "/regime/vcg";
    const { container } = render(<RegimePanel prices={{}} />);
    expect(within(container).getByTestId("vcg-panel-stub")).toBeTruthy();
    expect(within(container).queryByTestId("gex-panel-stub")).toBeNull();
  });

  it("renders the GEX panel when pathname is /regime/gex", () => {
    mockedPathname = "/regime/gex";
    const { container } = render(<RegimePanel prices={{}} />);
    expect(within(container).getByTestId("gex-panel-stub")).toBeTruthy();
    expect(within(container).queryByTestId("vcg-panel-stub")).toBeNull();
  });

  it("falls back to CRI for an unknown subpath", () => {
    mockedPathname = "/regime/bogus";
    const { container } = render(<RegimePanel prices={{}} />);
    expect(within(container).queryByTestId("vcg-panel-stub")).toBeNull();
    expect(within(container).queryByTestId("gex-panel-stub")).toBeNull();
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
