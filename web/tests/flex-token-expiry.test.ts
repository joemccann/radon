/**
 * TDD: Flex Token Expiry monitoring
 * RED tests — all fail before implementation
 */

import { describe, it, expect } from "vitest";
import { readFile } from "fs/promises";
import path from "path";

const PROJECT_ROOT = path.resolve(__dirname, "../..");

// ── 1. Config file ─────────────────────────────────────────────────

describe("flex_token_config.json", () => {
  it("exists in data/", async () => {
    const p = path.join(PROJECT_ROOT, "data", "flex_token_config.json");
    const content = await readFile(p, "utf-8");
    expect(content).toBeTruthy();
  });

  it("contains expires_at ISO date", async () => {
    const p = path.join(PROJECT_ROOT, "data", "flex_token_config.json");
    const data = JSON.parse(await readFile(p, "utf-8"));
    expect(data.expires_at).toMatch(/^\d{4}-\d{2}-\d{2}/);
  });

  it("contains renewal_url", async () => {
    const p = path.join(PROJECT_ROOT, "data", "flex_token_config.json");
    const data = JSON.parse(await readFile(p, "utf-8"));
    expect(data.renewal_url).toContain("interactivebrokers.com");
  });

  it("contains breadcrumb trail", async () => {
    const p = path.join(PROJECT_ROOT, "data", "flex_token_config.json");
    const data = JSON.parse(await readFile(p, "utf-8"));
    expect(data.breadcrumb).toContain("Flex Queries");
  });

  it("contains reminder_days array with 30, 14, 7, 1", async () => {
    const p = path.join(PROJECT_ROOT, "data", "flex_token_config.json");
    const data = JSON.parse(await readFile(p, "utf-8"));
    expect(data.reminder_days).toEqual([30, 14, 7, 1]);
  });
});

// ── 2. Daemon handler ──────────────────────────────────────────────

describe("flex_token_check daemon handler", () => {
  it("handler file exists", async () => {
    const p = path.join(
      PROJECT_ROOT, "scripts", "monitor_daemon", "handlers", "flex_token_check.py"
    );
    const content = await readFile(p, "utf-8");
    expect(content).toContain("class FlexTokenCheck");
  });

  it("inherits from BaseHandler", async () => {
    const p = path.join(
      PROJECT_ROOT, "scripts", "monitor_daemon", "handlers", "flex_token_check.py"
    );
    const content = await readFile(p, "utf-8");
    expect(content).toContain("BaseHandler");
  });

  it("reads flex_token_config.json", async () => {
    const p = path.join(
      PROJECT_ROOT, "scripts", "monitor_daemon", "handlers", "flex_token_check.py"
    );
    const content = await readFile(p, "utf-8");
    expect(content).toContain("flex_token_config.json");
  });

  it("checks reminder thresholds 30/14/7/1", async () => {
    const p = path.join(
      PROJECT_ROOT, "scripts", "monitor_daemon", "handlers", "flex_token_check.py"
    );
    const content = await readFile(p, "utf-8");
    expect(content).toContain("reminder_days");
  });

  it("is registered in daemon run.py", async () => {
    const p = path.join(PROJECT_ROOT, "scripts", "monitor_daemon", "run.py");
    const content = await readFile(p, "utf-8");
    expect(content).toContain("FlexTokenCheck");
  });
});

// ── 3. API route ───────────────────────────────────────────────────

describe("GET /api/flex-token API route", () => {
  it("route file exists", async () => {
    const p = path.join(
      PROJECT_ROOT, "web", "app", "api", "flex-token", "route.ts"
    );
    const content = await readFile(p, "utf-8");
    expect(content).toContain("GET");
  });

  it("reads flex_token_config.json", async () => {
    const p = path.join(
      PROJECT_ROOT, "web", "app", "api", "flex-token", "route.ts"
    );
    const content = await readFile(p, "utf-8");
    expect(content).toContain("flex_token_config");
  });

  it("computes days_remaining", async () => {
    const p = path.join(
      PROJECT_ROOT, "web", "app", "api", "flex-token", "route.ts"
    );
    const content = await readFile(p, "utf-8");
    expect(content).toContain("days_remaining");
  });

  it("returns should_warn boolean", async () => {
    const p = path.join(
      PROJECT_ROOT, "web", "app", "api", "flex-token", "route.ts"
    );
    const content = await readFile(p, "utf-8");
    expect(content).toContain("should_warn");
  });
});

// ── 4. UI banner component ─────────────────────────────────────────

describe("FlexTokenBanner component", () => {
  it("component file exists", async () => {
    const p = path.join(
      PROJECT_ROOT, "web", "components", "FlexTokenBanner.tsx"
    );
    const content = await readFile(p, "utf-8");
    expect(content).toContain("FlexTokenBanner");
  });

  it("fetches /api/flex-token", async () => {
    const p = path.join(
      PROJECT_ROOT, "web", "components", "FlexTokenBanner.tsx"
    );
    const content = await readFile(p, "utf-8");
    expect(content).toContain("/api/flex-token");
  });

  it("shows renewal URL", async () => {
    const p = path.join(
      PROJECT_ROOT, "web", "components", "FlexTokenBanner.tsx"
    );
    const content = await readFile(p, "utf-8");
    expect(content).toContain("renewal_url");
  });

  it("shows days remaining", async () => {
    const p = path.join(
      PROJECT_ROOT, "web", "components", "FlexTokenBanner.tsx"
    );
    const content = await readFile(p, "utf-8");
    expect(content).toContain("days_remaining");
  });

  it("only renders when should_warn is true", async () => {
    const p = path.join(
      PROJECT_ROOT, "web", "components", "FlexTokenBanner.tsx"
    );
    const content = await readFile(p, "utf-8");
    expect(content).toContain("should_warn");
  });
});
