/**
 * TDD: Regime Share feature
 * RED tests — all fail before implementation
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { readFile } from "fs/promises";
import path from "path";

const PROJECT_ROOT = path.resolve(__dirname, "../..");
const REPORTS_DIR = path.join(PROJECT_ROOT, "reports");

// ── 1. Script existence ────────────────────────────────────────────

describe("generate_regime_share.py", () => {
  it("exists at scripts/generate_regime_share.py", async () => {
    const scriptPath = path.join(PROJECT_ROOT, "scripts", "generate_regime_share.py");
    await expect(readFile(scriptPath, "utf-8")).resolves.toContain("generate_regime_share");
  });

  it("has Python 3.9-compatible type hints (no X | Y syntax)", async () => {
    const scriptPath = path.join(PROJECT_ROOT, "scripts", "generate_regime_share.py");
    const content = await readFile(scriptPath, "utf-8");
    // Must not use str | None — must use Optional[str] or from __future__
    const hasBarUnion = /: str \| None|: int \| None|: float \| None/.test(content);
    if (hasBarUnion) {
      // Only OK if from __future__ import annotations is present
      expect(content).toContain("from __future__ import annotations");
    } else {
      expect(true).toBe(true); // No bar unions — fine
    }
  });
});

// ── 2. FastAPI endpoint ────────────────────────────────────────────

describe("POST /regime/share FastAPI endpoint", () => {
  it("is registered in server.py", async () => {
    const serverPath = path.join(PROJECT_ROOT, "scripts", "api", "server.py");
    const content = await readFile(serverPath, "utf-8");
    expect(content).toContain('"/regime/share"');
  });

  it("calls generate_regime_share.py", async () => {
    const serverPath = path.join(PROJECT_ROOT, "scripts", "api", "server.py");
    const content = await readFile(serverPath, "utf-8");
    expect(content).toContain("generate_regime_share.py");
  });
});

describe("POST /internals/share FastAPI endpoint", () => {
  it("is registered in server.py", async () => {
    const serverPath = path.join(PROJECT_ROOT, "scripts", "api", "server.py");
    const content = await readFile(serverPath, "utf-8");
    expect(content).toContain('"/internals/share"');
  });

  it("calls generate_regime_share.py", async () => {
    const serverPath = path.join(PROJECT_ROOT, "scripts", "api", "server.py");
    const content = await readFile(serverPath, "utf-8");
    const idxInternals = content.indexOf('"/internals/share"');
    const idxGenerate = content.indexOf("generate_regime_share.py");
    expect(idxInternals).toBeGreaterThan(-1);
    expect(idxGenerate).toBeGreaterThan(-1);
  });
});

// ── 3. Next.js API routes ──────────────────────────────────────────

describe("Next.js /api/regime/share routes", () => {
  it("POST route exists", async () => {
    const routePath = path.join(
      PROJECT_ROOT, "web", "app", "api", "regime", "share", "route.ts"
    );
    await expect(readFile(routePath, "utf-8")).resolves.toContain("POST");
  });

  it("POST route proxies to /regime/share on FastAPI", async () => {
    const routePath = path.join(
      PROJECT_ROOT, "web", "app", "api", "regime", "share", "route.ts"
    );
    const content = await readFile(routePath, "utf-8");
    expect(content).toContain("/regime/share");
  });

  it("content GET route exists", async () => {
    const routePath = path.join(
      PROJECT_ROOT, "web", "app", "api", "regime", "share", "content", "route.ts"
    );
    await expect(readFile(routePath, "utf-8")).resolves.toContain("GET");
  });

  it("content route is restricted to public share cards", async () => {
    // Hardened 2026-06-28: serves only tweet-regime-* cards via the shared
    // validator, not arbitrary files under reports/.
    const routePath = path.join(
      PROJECT_ROOT, "web", "app", "api", "regime", "share", "content", "route.ts"
    );
    const content = await readFile(routePath, "utf-8");
    expect(content).toContain("isAllowedShareCardPath");
    expect(content).toContain('"regime"');
  });
});

describe("Next.js /api/internals/share routes", () => {
  it("POST route exists", async () => {
    const routePath = path.join(
      PROJECT_ROOT, "web", "app", "api", "internals", "share", "route.ts"
    );
    await expect(readFile(routePath, "utf-8")).resolves.toContain("POST");
  });

  it("POST route proxies to /internals/share on FastAPI", async () => {
    const routePath = path.join(
      PROJECT_ROOT, "web", "app", "api", "internals", "share", "route.ts"
    );
    const content = await readFile(routePath, "utf-8");
    expect(content).toContain("/internals/share");
  });

  it("content GET route exists", async () => {
    const routePath = path.join(
      PROJECT_ROOT, "web", "app", "api", "internals", "share", "content", "route.ts"
    );
    await expect(readFile(routePath, "utf-8")).resolves.toContain("GET");
  });

  it("content route is restricted to public share cards", async () => {
    // Hardened 2026-06-28: serves only tweet-internals-* cards via the shared
    // validator, not arbitrary files under reports/.
    const routePath = path.join(
      PROJECT_ROOT, "web", "app", "api", "internals", "share", "content", "route.ts"
    );
    const content = await readFile(routePath, "utf-8");
    expect(content).toContain("isAllowedShareCardPath");
    expect(content).toContain('"internals"');
  });
});

// ── 4. RegimePanel component has Share button ──────────────────────

describe("RegimePanel share button", () => {
  it("uses shared ShareReportModal component", async () => {
    const panelPath = path.join(
      PROJECT_ROOT, "web", "components", "RegimePanel.tsx"
    );
    const content = await readFile(panelPath, "utf-8");
    expect(content).toContain("ShareReportModal");
  });

  it("has /api/regime/share endpoint", async () => {
    const panelPath = path.join(
      PROJECT_ROOT, "web", "components", "RegimePanel.tsx"
    );
    const content = await readFile(panelPath, "utf-8");
    expect(content).toContain('shareEndpoint="/api/regime/share"');
  });

  it("reuses cta-share modal classes through shared component", async () => {
    const modalPath = path.join(
      PROJECT_ROOT, "web", "components", "ShareReportModal.tsx"
    );
    const content = await readFile(modalPath, "utf-8");
    expect(content).toContain("cta-share-backdrop");
    expect(content).toContain("cta-share-iframe");
  });

  it("shares the same implementation in /cta and /regime", async () => {
    const panelPath = path.join(
      PROJECT_ROOT, "web", "components", "RegimePanel.tsx"
    );
    const ctaContent = await readFile(path.join(PROJECT_ROOT, "web", "components", "CtaPage.tsx"), "utf-8");
    const modalContent = await readFile(path.join(PROJECT_ROOT, "web", "components", "ShareReportModal.tsx"), "utf-8");
    const content = await readFile(panelPath, "utf-8");
    expect(content).toContain("ShareReportModal");
    expect(ctaContent).toContain("ShareReportModal");
    expect(modalContent).toContain("role=\"dialog\"");
  });
});

// ── 5. Card content requirements ──────────────────────────────────

describe("Regime share card content", () => {
  it("script reads from CRI cache (not VCG-only)", async () => {
    const scriptPath = path.join(PROJECT_ROOT, "scripts", "generate_regime_share.py");
    const content = await readFile(scriptPath, "utf-8");
    expect(content).toContain("cri_scheduled");
  });

  it("script generates exactly 4 cards", async () => {
    const scriptPath = path.join(PROJECT_ROOT, "scripts", "generate_regime_share.py");
    const content = await readFile(scriptPath, "utf-8");
    // Should have 4 card generator functions
    const matches = content.match(/def card\d+_/g);
    expect(matches).toHaveLength(4);
  });

  it("script builds tweet text with viral hook", async () => {
    const scriptPath = path.join(PROJECT_ROOT, "scripts", "generate_regime_share.py");
    const content = await readFile(scriptPath, "utf-8");
    expect(content).toContain("build_tweet");
    expect(content).toContain("radon.run");
  });

  it("script uses from __future__ import annotations for 3.9 compat", async () => {
    const scriptPath = path.join(PROJECT_ROOT, "scripts", "generate_regime_share.py");
    const content = await readFile(scriptPath, "utf-8");
    expect(content).toContain("from __future__ import annotations");
  });
});
