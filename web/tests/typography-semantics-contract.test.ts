import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const WEB_ROOT = join(import.meta.dirname, "..");
const source = (path: string) => readFileSync(join(WEB_ROOT, path), "utf8");

describe("workspace typography semantics", () => {
  it("assigns the visible route label as h1 without duplicating route-owned headings", () => {
    const header = source("components/Header.tsx");
    const shell = source("components/WorkspaceShell.tsx");

    expect(header).toContain('<h1 className="rail-section" title={activeLabel}>{activeLabel}</h1>');
    for (const route of ["ticker-detail", "watchlist", "admin", "workflow"]) {
      expect(shell).toContain(`activeSection !== "${route}"`);
    }
  });

  it("uses h2 dashboard and workspace section headings above card h3 headings", () => {
    const dashboard = source("components/dashboard/DashboardSurface.tsx");
    const workspace = source("components/WorkspaceSections.tsx");

    expect(dashboard).toContain('<h2 className="dashboard-section__heading"');
    expect(workspace).toContain('<h2 className="section-title">');
    expect(source("components/dashboard/CatalystCard.tsx")).toContain('<h3 className="panel-title">');
  });

  it("exposes full operational strings hidden by visual ellipsis", () => {
    expect(source("components/ServiceHealthBanner.tsx")).toContain('title={fullMessage}');
    expect(source("components/ticker-detail/OptionsChainTab.tsx")).toContain('title={`${ticker} ${formatExpiry(leg.expiry)}');
    expect(source("components/dashboard/CatalystCard.tsx")).toContain('title={r.title}');
    expect(source("components/dashboard/OpportunitiesCard.tsx")).toContain('className="snapshot-row__signal" title=');
    expect(source("components/mobile/MobileAppBar.tsx")).toContain('className="mobile-app-bar__title" title={title}');
  });
});
