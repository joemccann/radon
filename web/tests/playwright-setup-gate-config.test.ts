/**
 * Contract: playwright.setup-gate.config.ts is a KEYLESS isolated compile-mode
 * browser job. Ambient Clerk / authless env on the runner must not leak in.
 */
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it, vi } from "vitest";

const webRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");

afterEach(() => {
  vi.unstubAllEnvs();
});

async function loadSetupGateConfig() {
  vi.resetModules();
  return (await import("../playwright.setup-gate.config")).default;
}

describe("playwright.setup-gate.config isolation", () => {
  it("stays keyless and isolated even when ambient env is keyed (T-435)", async () => {
    vi.stubEnv("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY", "pk_test_ambient");
    vi.stubEnv("CLERK_SECRET_KEY", "sk_test_ambient");
    vi.stubEnv("RADON_AUTHLESS_TEST", "1");
    vi.stubEnv("RADON_AUTHLESS_TEST_TOKEN", "ambient-token");
    vi.stubEnv("NEXT_PUBLIC_RADON_AUTHLESS_TEST", "1");
    vi.stubEnv("RADON_SETUP_COMPLETE", "1");
    vi.stubEnv("NEXT_DIST_DIR", ".next");
    vi.stubEnv("PLAYWRIGHT_PORT", "3000");

    const config = await loadSetupGateConfig();

    expect(config.use?.extraHTTPHeaders).toBeUndefined();
    expect(JSON.stringify(config)).not.toContain("x-radon-authless-test");

    const webServer = config.webServer;
    expect(webServer).toBeDefined();
    expect(Array.isArray(webServer)).toBe(false);
    const server = webServer as {
      env?: Record<string, string>;
      reuseExistingServer?: boolean;
    };
    const env = server.env ?? {};
    expect(env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY).toBe("");
    expect(env.CLERK_SECRET_KEY).toBe("");
    expect(env.RADON_AUTHLESS_TEST).toBe("");
    expect(env.RADON_AUTHLESS_TEST_TOKEN).toBe("");
    expect(env.NEXT_PUBLIC_RADON_AUTHLESS_TEST).toBe("");
    expect(env.RADON_SETUP_COMPLETE).toBe("");
    expect(env.NEXT_DIST_DIR).toBe(".next-setup-gate");
    expect(env.NEXT_DIST_DIR).not.toBe(".next");
    expect(server.reuseExistingServer).toBe(false);

    const baseURL = config.use?.baseURL ?? "";
    const port = Number(new URL(baseURL).port);
    expect(port).not.toBe(3000);
    expect(baseURL).toBe(`http://127.0.0.1:${port}`); // pragma: allowlist secret

    const testDir = resolve(webRoot, config.testDir ?? "");
    const e2eDir = resolve(webRoot, "e2e");
    expect(testDir).toBe(resolve(webRoot, "e2e-setup-gate"));
    expect(testDir).not.toBe(e2eDir);
    expect(testDir.startsWith(`${e2eDir}/`)).toBe(false);
  });
});
