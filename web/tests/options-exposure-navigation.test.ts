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
      href: "/options/net-gex?symbol=MU",
    });
  });

  it("mounts Net GEX in the Options workspace and preserves the legacy deep link", () => {
    const canonical = readFileSync(resolve(__dirname, "../app/options/net-gex/page.tsx"), "utf8");
    const legacy = readFileSync(resolve(__dirname, "../app/options/exposure/page.tsx"), "utf8");
    const mobileNavigation = readFileSync(resolve(__dirname, "../components/mobile/MobileMoreDrawer.tsx"), "utf8");

    expect(canonical).toContain('export const dynamic = "force-dynamic"');
    expect(canonical).toContain('<WorkspaceShell section="options"');
    expect(legacy).toContain('redirect(`/options/net-gex?symbol=${encodeURIComponent(symbol)}`)');
    expect(mobileNavigation).toContain('{ label: "Options", href: "/options/net-gex?symbol=MU"');
  });
});
