/**
 * Focused contracts for skill-stack P0/P1 shell chrome (PLAN.md T1–T12).
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const WEB = join(import.meta.dirname, "..");
const src = (rel: string) => readFileSync(join(WEB, rel), "utf8");

describe("skill-stack shell chrome", () => {
  it("removes the ruled left-edge treatment from every instrument shell", () => {
    const css = src("app/globals.css");
    expect(css).not.toContain("panel-edge-trace");

    for (const file of [
      "components/DashboardNewsFeed.tsx",
      "components/ScannerInstrumentShell.tsx",
      "components/alerts/AlertsPanel.tsx",
      "components/dashboard/CatalystsQuadrant.tsx",
      "components/dashboard/EngineStatePanel.tsx",
      "components/dashboard/ScannerHero.tsx",
      "components/flow-analysis/InformedFlowPanel.tsx",
      "components/instruments/InstrumentPanel.tsx",
      "components/ui/InstrumentSkeleton.tsx",
    ]) {
      expect(src(file)).not.toContain("panel-edge-trace");
    }
  });

  it("T10: optimizePackageImports includes lucide-react", () => {
    const config = src("next.config.mjs");
    expect(config).toMatch(/optimizePackageImports:\s*\[\s*["']lucide-react["']\s*\]/);
  });

  it("T6: WorkspaceSections dynamic import uses instrument skeleton", () => {
    const shell = src("components/WorkspaceShell.tsx");
    expect(shell).toContain("InstrumentSkeleton");
    expect(shell).not.toMatch(/loading:\s*\(\)\s*=>\s*null/);
    expect(src("app/scanner/loading.tsx")).toContain("InstrumentSkeleton");
  });

  it("T11: scanner sections omit live prices identity", () => {
    const shell = src("components/WorkspaceShell.tsx");
    expect(shell).toContain("sectionNeedsPrices");
    expect(shell).toContain("pricesForSections");
    expect(src("components/WorkspaceSections.tsx")).toMatch(/export default memo\(WorkspaceSections\)/);
  });

  it("T12: skip link, toast live region, sidebar aria-current, modal h2", () => {
    const shell = src("components/WorkspaceShell.tsx");
    expect(shell).toContain('href="#main-content"');
    expect(shell).toContain('id="main-content"');
    expect(shell).toContain('type="button"');

    const toast = src("components/Toast.tsx");
    // Each item owns its live-region semantics: status implies polite,
    // alert implies assertive. A wrapping status would double-announce errors.
    expect(toast).toContain('role={toast.type === "error" ? "alert" : "status"}');
    expect(toast).toContain("ToastViewport");

    const sidebar = src("components/Sidebar.tsx");
    expect(sidebar).toContain('aria-current={item.route === activeSection ? "page" : undefined}');

    const header = src("components/Header.tsx");
    expect(header).toContain('aria-live="polite"');
    expect(header).toContain('ariaLabel="Search ticker"');
    expect(header).toMatch(/type="button"/);

    const modal = src("components/Modal.tsx");
    expect(modal).toContain("<h2 className=\"modal-title\"");
    expect(modal).toContain("modal-backdrop--exiting");

    const css = src("app/globals.css");
    expect(css).toMatch(/\[data-theme="dark"\]\s*\{[^}]*color-scheme:\s*dark/s);
    expect(css).toMatch(/\[data-theme="light"\]\s*\{[^}]*color-scheme:\s*light/s);
    expect(css).toMatch(/\.modal-backdrop[^{]*\{[^}]*overscroll-behavior:\s*contain/s);
    expect(css).toMatch(/\.nav-group-label[^{]*\{[^}]*min-height:\s*var\(--hit-min\)/s);
  });

  it("T3: news feed uses instrument shell atoms", () => {
    const feed = src("components/DashboardNewsFeed.tsx");
    expect(feed).toContain("Feed / 01");
    expect(feed).toContain("Live market analysis");
    expect(feed).toContain("panel-meta-rail");
    expect(feed).toContain("capture.basis");
    expect(feed).not.toMatch(/from "lucide-react".*Radio|Radio size=/);
  });

  it("feed rail polish stays scoped and leaves the shared rail bytes unchanged", () => {
    const css = src("app/globals.css");
    const feedRail = css.indexOf(".dashboard-news__rail");
    expect(feedRail).toBeGreaterThan(0);
    const chunk = css.slice(feedRail, feedRail + 5000);
    expect(chunk).toContain("grid-template-columns");
    expect(chunk).toContain("tabular-nums");
    expect(chunk).toContain("border-radius: 0 0 3px 3px");

    const globalStart = css.indexOf("\n.panel-meta-rail {");
    expect(globalStart).toBeGreaterThan(0);
    const globalEnd = css.indexOf("\n}", globalStart);
    expect(css.slice(globalStart + 1, globalEnd + 2)).toBe(
      [
        ".panel-meta-rail {",
        "  display: flex;",
        "  flex-wrap: wrap;",
        "  align-items: center;",
        "  gap: 18px;",
        "  padding: 10px 16px;",
        "  border-top: 1px solid var(--line-grid);",
        "  font-family: var(--font-mono);",
        "  font-size: 11px;",
        "  letter-spacing: 0.04em;",
        "  color: var(--text-secondary);",
        "}",
      ].join("\n"),
    );

    const itemStart = css.indexOf("\n.panel-meta-rail-item {");
    const itemEnd = css.indexOf("\n}", itemStart);
    expect(css.slice(itemStart + 1, itemEnd + 2)).toBe(
      [
        ".panel-meta-rail-item {",
        "  display: inline-flex;",
        "  align-items: baseline;",
        "  gap: 8px;",
        "  white-space: nowrap;",
        "}",
      ].join("\n"),
    );

    const keyStart = css.indexOf("\n.panel-meta-rail-item .k {");
    const keyEnd = css.indexOf("\n}", keyStart);
    expect(css.slice(keyStart + 1, keyEnd + 2)).toBe(
      [
        ".panel-meta-rail-item .k {",
        "  font-size: 9px;",
        "  text-transform: uppercase;",
        "  letter-spacing: 0.14em;",
        "  color: var(--text-muted);",
        "}",
      ].join("\n"),
    );

    const valueStart = css.indexOf("\n.panel-meta-rail-item .v {");
    const valueEnd = css.indexOf("\n}", valueStart);
    expect(css.slice(valueStart + 1, valueEnd + 2)).toBe(
      [
        ".panel-meta-rail-item .v {",
        "  color: var(--text-secondary);",
        "}",
      ].join("\n"),
    );

    const clear = src("app/clear.css");
    expect(clear).toContain(
      [
        ".radon-clear :is(.panel-meta-rail, .instrument-section__rail, .performance-chart-meta) {",
        "  background: var(--bg-subtle);",
        "  border-color: var(--line-grid);",
        "  font-family: var(--font-sans);",
        "  font-size: var(--text-meta);",
        "  letter-spacing: 0;",
        "}",
        "",
        ".radon-clear :is(.panel-meta-rail-item .k, .performance-meta-label) {",
        "  font-size: var(--text-meta);",
        "  letter-spacing: 0;",
        "  text-transform: none;",
        "}",
      ].join("\n"),
    );
  });

  it("T4: dashboard panels expose meta rails", () => {
    for (const file of [
      "components/DashboardNewsFeed.tsx",
      "components/dashboard/ScannerHero.tsx",
      "components/dashboard/CatalystsQuadrant.tsx",
      "components/dashboard/EngineStatePanel.tsx",
    ]) {
      const body = src(file);
      expect(body).toContain("panel-meta-rail");
    }
  });

  it("T2/T5: dashboard section outer chrome collapses labels when open", () => {
    const dash = src("components/dashboard/DashboardSurface.tsx");
    expect(dash).toContain("showDeviceLabel");
    expect(dash).toContain("dashboard-section__label");
    expect(dash).toContain("collapseOnly");
  });

  it("T7: scanner sync hooks skip inactive mount GETs", () => {
    expect(src("lib/useSyncHook.ts")).toContain("loadWhenInactive");
    for (const file of [
      "lib/useScanner.ts",
      "lib/useThetaHarvester.ts",
      "lib/useStrengthConfirmation.ts",
      "lib/useLeap.ts",
      "lib/useGarchConvergence.ts",
      "lib/useDiscover.ts",
    ]) {
      expect(src(file)).toContain("loadWhenInactive: false");
    }
  });

  it("T8/T9: drawer and sheet/modal exit phases exist", () => {
    expect(src("components/mobile/MobileMoreDrawer.tsx")).toContain("mobile-drawer-root--exiting");
    expect(src("components/mobile/BottomSheet.tsx")).toContain("mobile-sheet-root--exiting");
    expect(src("components/mobile/BottomSheet.tsx")).toContain("SETTLE_MS");
    const css = src("app/globals.css");
    expect(css).toContain("mobile-drawer-root--open");
    expect(css).toContain("mobile-sheet-root--exiting");
  });

  it("motion: share popover, admin confirm exit, toast transitions", () => {
    const share = src("components/SharePnlButton.tsx");
    expect(share).toContain("share-pnl-popover--exiting");
    expect(share).toContain("POPOVER_EXIT_MS");

    const confirm = src("components/admin/ConfirmDialog.tsx");
    expect(confirm).toContain("admin-confirm-backdrop--exiting");
    expect(confirm).toContain("EXIT_MS");

    const css = src("app/globals.css");
    // Toast uses interruptible transitions (not keyframe in/out).
    expect(css).toMatch(/\.toast[^{]*\{[^}]*transition:\s*transform 200ms var\(--ease-out\),\s*opacity 200ms var\(--ease-out\)/s);
    expect(css).toContain("share-pnl-popover--exiting");
    expect(css).toContain("admin-confirm-backdrop--exiting");
    expect(css).not.toMatch(/@keyframes toast-in/);
    expect(css).not.toMatch(/@keyframes toast-out/);
    // No soft shadow on share popover (brand lock).
    const shareIdx = css.indexOf(".share-pnl-popover {");
    expect(shareIdx).toBeGreaterThan(0);
    const shareBlock = css.slice(shareIdx, shareIdx + 700);
    expect(shareBlock).not.toMatch(/box-shadow/);
    expect(shareBlock).toContain("transform-origin: bottom right");
    expect(shareBlock).toContain("scale(0.97)");
  });
});
