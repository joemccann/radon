import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const WEB = join(import.meta.dirname, "..");
const source = (path: string) => readFileSync(join(WEB, path), "utf8");

describe("portfolio startup performance contracts", () => {
  it("disables automatic Next.js prefetch on persistent shell navigation", () => {
    for (const path of [
      "components/Sidebar.tsx",
      "components/mobile/MobileAppBar.tsx",
      "components/mobile/MobileMoreDrawer.tsx",
      "components/mobile/MobileTabBar.tsx",
    ]) {
      const linkTags = source(path).match(/<Link\b[\s\S]*?>/g) ?? [];
      expect(linkTags.length, `${path} should contain navigation links`).toBeGreaterThan(0);
      for (const tag of linkTags) {
        expect(tag, `${path} must opt each shell link out of viewport prefetch`).toContain("prefetch={false}");
      }
    }
  });

  it("loads the portfolio surface without the all-routes workspace chunk", () => {
    const shell = source("components/WorkspaceShell.tsx");
    const workspace = source("components/WorkspaceSections.tsx");

    expect(shell).toContain('dynamic(() => import("@/components/PortfolioSections")');
    expect(shell).toMatch(/activeSection === "portfolio"[\s\S]*<PortfolioSections/);
    expect(workspace).not.toContain('from "./PortfolioSections"');
    expect(workspace).toMatch(/case "portfolio":[\s\S]*return null;/);
    expect(workspace).not.toContain("function PortfolioSections(");
    expect(workspace).not.toContain('from "./PositionTable"');
  });

  it("seeds the portfolio page on the server and enriches entry dates only for orders", () => {
    const page = source("app/portfolio/page.tsx");
    const shell = source("components/WorkspaceShell.tsx");

    expect(page).toContain('export const dynamic = "force-dynamic"');
    expect(page).toContain("readPortfolioSnapshotSeed");
    expect(page).not.toContain("fetch(");
    expect(page).toContain('process.env.RADON_AUTHLESS_TEST === "1"');
    expect(shell).toContain("initialSnapshot: initialPortfolio");
    expect(shell).toContain("includeEntryDates: isOrdersPage");
  });

  it("keeps FRED freshness in a bounded server cache", () => {
    expect(source("app/api/risk-free-rate/route.ts")).toContain(
      "next: { revalidate: REVALIDATE_SECONDS }",
    );
  });
});
