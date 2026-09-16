import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { describe, expect, it } from "vitest";

import { navItems } from "../lib/data";
import { REGIME_TAB_LABEL, REGIME_TABS } from "../lib/regimeRail";
import {
  DEFAULT_DOCUMENT_TITLE,
  STATIC_ROUTE_TITLES,
  TITLE_TEMPLATE,
  documentTitle,
  metadataForPath,
  titleForPathname,
} from "../lib/pageTitle";

const WEB_ROOT = join(__dirname, "..");
const APP_ROOT = join(WEB_ROOT, "app");

const DYNAMIC_TITLE_PAGES = new Set([
  "app/[ticker]/page.tsx",
  "app/flow-analysis/[ticker]/page.tsx",
  "app/options/net-gex/page.tsx",
  "app/options/rv-ratio/page.tsx",
  "app/scanner/page.tsx",
]);

const CLIENT_LAYOUT_PAGES = new Set(["app/kit/page.tsx"]);

function walkPages(dir: string): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) out.push(...walkPages(p));
    else if (name === "page.tsx") out.push(p);
  }
  return out;
}

function routeFromPage(rel: string): string {
  const inner = rel.replace(/^app\//, "").replace(/\/page\.tsx$/, "").replace(/^page\.tsx$/, "");
  if (!inner) return "/";
  const segs = inner.split("/").filter((seg) => !(seg.startsWith("[") && seg.endsWith("]")));
  return segs.length === 0 ? "/" : `/${segs.join("/")}`;
}

describe("document title helper", () => {
  it("puts the page name first so truncated tabs stay distinct", () => {
    expect(TITLE_TEMPLATE.startsWith("%s")).toBe(true);
    expect(documentTitle("Orders")).toBe("Orders · Radon");
    expect(documentTitle("Positions")).toBe("Positions · Radon");
    expect(DEFAULT_DOCUMENT_TITLE).toBe("Radon Terminal");
  });

  it("titles primary workspace routes with the in-app labels", () => {
    expect(titleForPathname("/")).toBe("Portfolio");
    expect(titleForPathname("/dashboard")).toBe("Portfolio");
    expect(titleForPathname("/portfolio")).toBe("Positions");
    expect(titleForPathname("/orders")).toBe("Orders");
    expect(titleForPathname("/performance")).toBe("Performance");
    expect(titleForPathname("/watchlist")).toBe("Watchlist");
    expect(titleForPathname("/journal")).toBe("Journal");
    expect(titleForPathname("/research-workbench")).toBe("Research workbench");
    expect(titleForPathname("/admin")).toBe("Operator");
  });

  it("titles nested research, options, and auth routes", () => {
    expect(titleForPathname("/flow-analysis")).toBe("Flow Analysis");
    expect(titleForPathname("/flow-analysis/nvda")).toBe("NVDA Flow");
    expect(titleForPathname("/options/net-gex")).toBe("Net GEX");
    expect(titleForPathname("/options/net-gex", new URLSearchParams("symbol=spx"))).toBe("SPX Net GEX");
    expect(titleForPathname("/options/rv-ratio", new URLSearchParams("symbol=QQQ"))).toBe("QQQ Rel Vol");
    expect(titleForPathname("/scanner", new URLSearchParams("mode=discover"))).toBe("Discover");
    expect(titleForPathname("/scanner", new URLSearchParams("mode=vol-cone"))).toBe("Vol Cone");
    expect(titleForPathname("/scanner", new URLSearchParams("mode=vol-skew-mr"))).toBe("Vol/Skew MR");
    expect(titleForPathname("/sign-in")).toBe("Sign in");
    expect(titleForPathname("/demo-pending")).toBe("Setting up your demo");
  });

  it("titles each regime tab from the rail label", () => {
    expect(titleForPathname("/regime/cri")).toBe("CRI");
    expect(titleForPathname("/regime/llm")).toBe("AI infrastructure");
    expect(titleForPathname("/regime/calm-streak")).toBe("CALM STREAK");
    for (const tab of REGIME_TABS) {
      expect(titleForPathname(`/regime/${tab}`)).toBe(REGIME_TAB_LABEL[tab]);
    }
  });

  it("does not resolve inherited object properties as page titles", async () => {
    for (const mode of ["constructor", "toString", "__proto__", "unknown"]) {
      await expect(metadataForPath("/scanner", Promise.resolve({ mode })))
        .resolves.toEqual({ title: "Scanner" });
      expect(titleForPathname(`/regime/${mode}`)).toBe(DEFAULT_DOCUMENT_TITLE);
    }
  });

  it.each([
    ["/options/net-gex", "Net GEX"],
    ["/options/exposure", "Net GEX"],
    ["/options/rv-ratio", "Rel Vol"],
  ])("keeps %s titles aligned with the page's accepted symbols", async (path, label) => {
    for (const symbol of ["brk.b", "brk-b", "vix3m", "ABC1234567"]) {
      await expect(metadataForPath(path, Promise.resolve({ symbol })))
        .resolves.toEqual({ title: `${symbol.toUpperCase()} ${label}` });
    }
    for (const symbol of ["BAD!", "NVDA:US", "1ABC", "ABCDEFGHIJK", "nv da", ""]) {
      await expect(metadataForPath(path, Promise.resolve({ symbol })))
        .resolves.toEqual({ title: label });
    }
    // The page consumes the first repeated parameter, even if a later one is valid.
    await expect(metadataForPath(path, Promise.resolve({ symbol: ["BAD!", "NVDA"] })))
      .resolves.toEqual({ title: label });
  });

  it("titles instrument routes with the ticker", () => {
    expect(titleForPathname("/AAPL")).toBe("AAPL");
    expect(titleForPathname("/vix")).toBe("VIX");
  });

  it("builds generateMetadata payloads from the path and search params", async () => {
    await expect(metadataForPath("/scanner", Promise.resolve({ mode: "leap" })))
      .resolves.toEqual({ title: "LEAP" });
    await expect(metadataForPath("/options/net-gex", Promise.resolve({ symbol: "spx" })))
      .resolves.toEqual({ title: "SPX Net GEX" });
    await expect(metadataForPath("/orders")).resolves.toEqual({ title: "Orders" });
  });

  it("keeps nav labels aligned with static titles except more-specific nested routes", () => {
    for (const item of navItems) {
      if (item.href.startsWith("/regime/")) continue;
      expect(titleForPathname(item.href)).toBe(item.label);
    }
  });
});

describe("every App Router page exports a contextual document title", () => {
  const pages = walkPages(APP_ROOT).map((file) => relative(WEB_ROOT, file).replaceAll("\\", "/"));

  it("covers every page.tsx under app/", () => {
    expect(pages.length).toBeGreaterThan(40);
    expect(pages).toContain("app/orders/page.tsx");
    expect(pages).toContain("app/portfolio/page.tsx");
    expect(pages).toContain("app/kit/page.tsx");
  });

  it("uses routeMetadata or generateMetadata on every page", () => {
    const violations: string[] = [];

    for (const rel of pages) {
      if (CLIENT_LAYOUT_PAGES.has(rel)) {
        const layoutRel = rel.replace(/page\.tsx$/, "layout.tsx");
        let layout = "";
        try {
          layout = readFileSync(join(WEB_ROOT, layoutRel), "utf8");
        } catch {
          violations.push(`${rel}: client page missing ${layoutRel}`);
          continue;
        }
        const route = routeFromPage(rel);
        if (!layout.includes(`routeMetadata("${route}")`)) {
          violations.push(`${rel}: client page missing layout routeMetadata("${route}")`);
        }
        continue;
      }

      const src = readFileSync(join(WEB_ROOT, rel), "utf8");
      if (DYNAMIC_TITLE_PAGES.has(rel)) {
        if (!src.includes("generateMetadata") || !/routeMetadata\(|metadataForPath\(/.test(src)) {
          violations.push(`${rel}: dynamic title page must export generateMetadata via routeMetadata/metadataForPath`);
        }
        continue;
      }

      const route = routeFromPage(rel);
      if (!src.includes(`routeMetadata("${route}")`)) {
        violations.push(`${rel}: expected routeMetadata("${route}")`);
      }
    }

    expect(violations).toEqual([]);
  });

  it("keeps a static title entry for every non-dynamic filesystem route", () => {
    const missing: string[] = [];
    for (const rel of pages) {
      if (rel.includes("[")) continue;
      const route = routeFromPage(rel);
      if (route.startsWith("/regime/") && route !== "/regime/vol-cone") continue;
      if (!(route in STATIC_ROUTE_TITLES)) missing.push(`${rel} -> ${route}`);
    }
    expect(missing).toEqual([]);
  });
});

describe("root layout title template", () => {
  it("applies the shared template so child pages render as Page · Radon", () => {
    const src = readFileSync(join(APP_ROOT, "layout.tsx"), "utf8");
    expect(src).toContain("DEFAULT_DOCUMENT_TITLE");
    expect(src).toContain("TITLE_TEMPLATE");
    expect(src).toContain("default: DEFAULT_DOCUMENT_TITLE");
    expect(src).toContain("template: TITLE_TEMPLATE");
  });
});
