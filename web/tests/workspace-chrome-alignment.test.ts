/**
 * @vitest-environment jsdom
 */

import { createElement } from "react";
import { describe, expect, it, vi } from "vitest";
import { render } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import Sidebar from "../components/Sidebar";

vi.mock("@/lib/IBStatusContext", () => ({
  useIBStatusContext: () => ({ displayStatus: "unreachable" }),
}));

vi.mock("@/lib/useProfile", () => ({
  useProfile: () => ({ profile: { username: "Operator", avatar_url: null } }),
}));

const globalsCss = readFileSync(join(__dirname, "..", "app", "globals.css"), "utf8");

function ruleBlock(selector: string): string {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const pattern = new RegExp(`(?:^|\\n)${escaped}\\s*\\{([^}]*)\\}`, "m");
  const match = globalsCss.match(pattern);
  if (!match) throw new Error(`rule not found for selector ${selector}`);
  return match[1];
}

function declaredHeight(selector: string): string | null {
  const body = ruleBlock(selector);
  const direct = body.match(/(?:^|;|\n)\s*height\s*:\s*([^;]+?)\s*(?:;|$)/);
  if (direct) return direct[1].trim();
  const min = body.match(/(?:^|;|\n)\s*min-height\s*:\s*([^;]+?)\s*(?:;|$)/);
  return min ? min[1].trim() : null;
}

describe("workspace chrome alignment", () => {
  it("sidebar-header and header use the same height token so bottom borders align", () => {
    const sidebarHeaderHeight = declaredHeight(".sidebar-header");
    const topHeaderHeight = declaredHeight(".header");

    expect(sidebarHeaderHeight).toBe("var(--header-height)");
    expect(topHeaderHeight).toBe("var(--header-height)");
    expect(sidebarHeaderHeight).toBe(topHeaderHeight);
  });

  it("sidebar-header does not reintroduce a min-height that would overflow the rail", () => {
    const body = ruleBlock(".sidebar-header");
    expect(body).not.toMatch(/min-height\s*:/);
  });

  it("renders the monogram at the compact 22px brand size", () => {
    const { container } = render(
      createElement(Sidebar, {
        activeSection: "portfolio",
        actionTone: "#05AD98",
        ibConnected: false,
        lastSync: null,
      }),
    );

    const monogram = container.querySelector(".sidebar-header img.logo-mark");
    expect(monogram).not.toBeNull();
    expect(monogram?.getAttribute("width")).toBe("22");
    expect(monogram?.getAttribute("height")).toBe("22");
  });
});
