/**
 * R-622 (P2, NF-3): when FastAPI is unreachable `fetchRegistry()` returns
 * null and the per-service field allowlist was replaced by the flat
 * `WEB_ENV_KEYS` union, so `if (!known)` could never fire and ANY id matching
 * SERVICE_PATTERN was accepted — contradicting the route's own contract that
 * every id and field is checked against GET /credentials before any store
 * call. It bites in the common case: FastAPI not yet up during first-run
 * setup is the exact branch the route's fallback exists for.
 *
 * R-629 (P3, NF-10): the setup token has no expiry. `generated`/`consumed`
 * are process state and only a successful completion invalidates, so an
 * abandoned wizard leaves a valid credential-writing token alive for the
 * whole process lifetime.
 */
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";

describe("R-629: the setup token has a bounded lifetime", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.useFakeTimers();
    delete process.env.RADON_SETUP_TOKEN;
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("verifies inside its TTL", async () => {
    const mod = await import("../lib/setup/setupToken");
    const token = mod.getSetupToken();
    vi.advanceTimersByTime(60_000);
    expect(mod.verifySetupToken(token)).toBe(true);
  });

  it("stops verifying past its TTL", async () => {
    const mod = await import("../lib/setup/setupToken");
    const token = mod.getSetupToken();
    vi.advanceTimersByTime(mod.SETUP_TOKEN_TTL_MS + 1_000);
    expect(mod.verifySetupToken(token)).toBe(false);
  });

  it("expires an env-provided token on the same clock", async () => {
    process.env.RADON_SETUP_TOKEN = "operator-supplied-token";
    const mod = await import("../lib/setup/setupToken");
    const token = mod.getSetupToken();
    expect(mod.verifySetupToken(token)).toBe(true);
    vi.advanceTimersByTime(mod.SETUP_TOKEN_TTL_MS + 1_000);
    expect(mod.verifySetupToken(token)).toBe(false);
  });

  it("still refuses a consumed token inside the TTL", async () => {
    const mod = await import("../lib/setup/setupToken");
    const token = mod.getSetupToken();
    mod.consumeSetupToken();
    expect(mod.verifySetupToken(token)).toBe(false);
  });
});
