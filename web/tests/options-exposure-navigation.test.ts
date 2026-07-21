import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

import { resolveSectionFromPath } from "@/lib/chat";
import { navItems } from "@/lib/data";

describe("options workspace wiring", () => {
  it("resolves canonical and legacy nested URLs before ticker-detail fallback", () => {
    expect(resolveSectionFromPath("/options/net-gex", "dashboard")).toBe("options");
    expect(resolveSectionFromPath("/options/exposure", "dashboard")).toBe("options");
  });

  it("publishes the broad Options workspace at its canonical Net GEX entry", () => {
    expect(navItems.find((item) => item.route === "options")).toMatchObject({
      label: "Options",
      href: "/options",
    });
  });

  it("mounts the Rel Vol tab ahead of the untouched reserved volatility slot", () => {
    expect(resolveSectionFromPath("/options/rv-ratio", "dashboard")).toBe("options");

    const page = readFileSync(resolve(__dirname, "../app/options/rv-ratio/page.tsx"), "utf8");
    expect(page).toContain('export const dynamic = "force-dynamic"');
    expect(page).toContain('<WorkspaceShell section="options"');

    const workspace = readFileSync(resolve(__dirname, "../components/OptionsWorkspacePanel.tsx"), "utf8");
    const relVolIdx = workspace.indexOf('{ id: "rv-ratio", label: "Rel Vol", available: true }');
    const volatilityIdx = workspace.indexOf('{ id: "volatility", label: "VIX / Volatility", available: false }');
    expect(relVolIdx).toBeGreaterThan(-1);
    // The reserved VIX/term-structure slot stays untouched, AFTER the new tab.
    expect(volatilityIdx).toBeGreaterThan(relVolIdx);
    // Tab activation + symbol carry-through route through /options/rv-ratio.
    expect(workspace).toContain('"/options/rv-ratio"');
  });

  it("mounts Net GEX in the Options workspace and preserves the legacy deep link", () => {
    const canonical = readFileSync(resolve(__dirname, "../app/options/net-gex/page.tsx"), "utf8");
    const legacy = readFileSync(resolve(__dirname, "../app/options/exposure/page.tsx"), "utf8");
    const root = readFileSync(resolve(__dirname, "../app/options/page.tsx"), "utf8");
    const mobileNavigation = readFileSync(resolve(__dirname, "../components/mobile/MobileMoreDrawer.tsx"), "utf8");

    expect(canonical).toContain('export const dynamic = "force-dynamic"');
    expect(canonical).toContain('<WorkspaceShell section="options"');
    expect(legacy).toContain("redirect(destination)");
    expect(root).toContain('redirect(symbol ? `/options/net-gex?symbol=${encodeURIComponent(symbol)}` : "/options/net-gex")');
    expect(mobileNavigation).toContain('{ label: "Options", href: "/options"');
  });
});
