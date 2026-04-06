/**
 * TDD: GEX Share feature
 * Mirrors regime-share.test.ts — same 4-layer contract.
 */

import { describe, it, expect } from "vitest";
import { readFile } from "fs/promises";
import path from "path";

const PROJECT_ROOT = path.resolve(__dirname, "../..");

// ── 1. Share script ────────────────────────────────────────────────

describe("generate_gex_share.py", () => {
  it("exists at scripts/generate_gex_share.py", async () => {
    const p = path.join(PROJECT_ROOT, "scripts", "generate_gex_share.py");
    await expect(readFile(p, "utf-8")).resolves.toContain("generate_gex_share");
  });

  it("uses from __future__ import annotations for Python 3.9 compat", async () => {
    const p = path.join(PROJECT_ROOT, "scripts", "generate_gex_share.py");
    const content = await readFile(p, "utf-8");
    expect(content).toContain("from __future__ import annotations");
  });

  it("reads from data/gex.json", async () => {
    const p = path.join(PROJECT_ROOT, "scripts", "generate_gex_share.py");
    const content = await readFile(p, "utf-8");
    expect(content).toContain("gex.json");
  });

  it("generates exactly 4 cards", async () => {
    const p = path.join(PROJECT_ROOT, "scripts", "generate_gex_share.py");
    const content = await readFile(p, "utf-8");
    // 4 card generator functions defined
    expect(content).toContain("card1_");
    expect(content).toContain("card2_");
    expect(content).toContain("card3_");
    expect(content).toContain("card4_");
  });

  it("outputs preview_path in JSON result", async () => {
    const p = path.join(PROJECT_ROOT, "scripts", "generate_gex_share.py");
    const content = await readFile(p, "utf-8");
    expect(content).toContain("preview_path");
  });

  it("builds tweet text with ticker, spot, net GEX, and radon.run brand", async () => {
    const p = path.join(PROJECT_ROOT, "scripts", "generate_gex_share.py");
    const content = await readFile(p, "utf-8");
    expect(content).toContain("build_tweet");
    expect(content).toContain("radon.run");
    expect(content).toContain("net_gex");
  });

  it("uses Radon brand colours (#0a0f14, #05AD98)", async () => {
    const p = path.join(PROJECT_ROOT, "scripts", "generate_gex_share.py");
    const content = await readFile(p, "utf-8");
    expect(content).toContain("#0a0f14");
    expect(content).toContain("#05AD98");
  });
});

// ── 2. FastAPI endpoint ────────────────────────────────────────────

describe("POST /gex/share FastAPI endpoint", () => {
  it("is registered in server.py", async () => {
    const p = path.join(PROJECT_ROOT, "scripts", "api", "server.py");
    const content = await readFile(p, "utf-8");
    expect(content).toContain('"/gex/share"');
  });

  it("calls generate_gex_share.py", async () => {
    const p = path.join(PROJECT_ROOT, "scripts", "api", "server.py");
    const content = await readFile(p, "utf-8");
    expect(content).toContain("generate_gex_share.py");
  });

  it("raises HTTPException on script failure", async () => {
    const p = path.join(PROJECT_ROOT, "scripts", "api", "server.py");
    const content = await readFile(p, "utf-8");
    // Verify same pattern as vcg/regime share — raises on not result.ok
    const gexShareIdx = content.indexOf('"/gex/share"');
    const snippet = content.slice(gexShareIdx, gexShareIdx + 400);
    expect(snippet).toContain("HTTPException");
  });
});

// ── 3. Next.js POST share route ────────────────────────────────────

describe("Next.js /api/gex/share POST route", () => {
  it("exists", async () => {
    const p = path.join(PROJECT_ROOT, "web", "app", "api", "gex", "share", "route.ts");
    await expect(readFile(p, "utf-8")).resolves.toContain("POST");
  });

  it("proxies to /gex/share on FastAPI", async () => {
    const p = path.join(PROJECT_ROOT, "web", "app", "api", "gex", "share", "route.ts");
    const content = await readFile(p, "utf-8");
    expect(content).toContain("/gex/share");
  });

  it("forwards Clerk auth token", async () => {
    const p = path.join(PROJECT_ROOT, "web", "app", "api", "gex", "share", "route.ts");
    const content = await readFile(p, "utf-8");
    expect(content).toContain("getToken");
  });

  it("propagates upstream HTTP status on RadonApiError", async () => {
    const p = path.join(PROJECT_ROOT, "web", "app", "api", "gex", "share", "route.ts");
    const content = await readFile(p, "utf-8");
    expect(content).toContain("RadonApiError");
    expect(content).toContain("err.status");
  });
});

// ── 4. Next.js GET content route ───────────────────────────────────

describe("Next.js /api/gex/share/content GET route", () => {
  it("exists", async () => {
    const p = path.join(PROJECT_ROOT, "web", "app", "api", "gex", "share", "content", "route.ts");
    await expect(readFile(p, "utf-8")).resolves.toContain("GET");
  });

  it("is sandboxed to REPORTS_DIR", async () => {
    const p = path.join(PROJECT_ROOT, "web", "app", "api", "gex", "share", "content", "route.ts");
    const content = await readFile(p, "utf-8");
    expect(content).toContain("REPORTS_DIR");
    expect(content).toContain("startsWith");
  });

  it("returns 403 for paths outside reports dir", async () => {
    const p = path.join(PROJECT_ROOT, "web", "app", "api", "gex", "share", "content", "route.ts");
    const content = await readFile(p, "utf-8");
    expect(content).toContain("403");
  });

  it("returns 400 when path param is missing", async () => {
    const p = path.join(PROJECT_ROOT, "web", "app", "api", "gex", "share", "content", "route.ts");
    const content = await readFile(p, "utf-8");
    expect(content).toContain("400");
  });
});
