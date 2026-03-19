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

  it("content route is sandboxed to reports directory", async () => {
    const routePath = path.join(
      PROJECT_ROOT, "web", "app", "api", "regime", "share", "content", "route.ts"
    );
    const content = await readFile(routePath, "utf-8");
    expect(content).toContain("REPORTS_DIR");
    expect(content).toContain("startsWith");
  });
});

// ── 4. RegimePanel component has Share button ──────────────────────

describe("RegimePanel share button", () => {
  it("imports Share2 from lucide-react", async () => {
    const panelPath = path.join(
      PROJECT_ROOT, "web", "components", "RegimePanel.tsx"
    );
    const content = await readFile(panelPath, "utf-8");
    expect(content).toContain("Share2");
  });

  it("has handleShare function", async () => {
    const panelPath = path.join(
      PROJECT_ROOT, "web", "components", "RegimePanel.tsx"
    );
    const content = await readFile(panelPath, "utf-8");
    expect(content).toContain("handleShare");
  });

  it("has share modal state (shareUrl, modalOpen)", async () => {
    const panelPath = path.join(
      PROJECT_ROOT, "web", "components", "RegimePanel.tsx"
    );
    const content = await readFile(panelPath, "utf-8");
    expect(content).toContain("shareUrl");
    expect(content).toContain("modalOpen");
  });

  it("calls /api/regime/share", async () => {
    const panelPath = path.join(
      PROJECT_ROOT, "web", "components", "RegimePanel.tsx"
    );
    const content = await readFile(panelPath, "utf-8");
    expect(content).toContain("/api/regime/share");
  });

  it("renders cta-share-backdrop modal class", async () => {
    const panelPath = path.join(
      PROJECT_ROOT, "web", "components", "RegimePanel.tsx"
    );
    const content = await readFile(panelPath, "utf-8");
    expect(content).toContain("cta-share-backdrop");
  });

  it("renders iframe with blob url", async () => {
    const panelPath = path.join(
      PROJECT_ROOT, "web", "components", "RegimePanel.tsx"
    );
    const content = await readFile(panelPath, "utf-8");
    expect(content).toContain("cta-share-iframe");
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
