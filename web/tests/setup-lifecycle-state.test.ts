// First-run setup lifecycle as one explicit machine (audit 2026-11-03 §3.5).
//
// setup → misconfigured → configured is derived from the Clerk key pair and
// the completion latch by ONE function, so a half-configured key pair can no
// longer disable both gates and fall through to a Clerk middleware that has
// no secret to verify with.
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  resolveSetupPhase,
  isSetupMode,
  isAuthMisconfigured,
} from "@/lib/setup/setupMode";
import {
  SETUP_TOKEN_TTL_MS,
  getSetupToken,
  verifySetupToken,
  __resetSetupTokenForTests,
} from "@/lib/setup/setupToken";

describe("resolveSetupPhase", () => {
  it("no keys + no latch → setup", () => {
    expect(resolveSetupPhase(undefined, " ", undefined)).toBe("setup");
  });
  it("both keys → configured, latched or not", () => {
    expect(resolveSetupPhase("pk_x", "sk_x", undefined)).toBe("configured");
    expect(resolveSetupPhase("pk_x", "sk_x", "1")).toBe("configured");
  });
  it("latch set but no keys → misconfigured", () => {
    expect(resolveSetupPhase(undefined, undefined, "1")).toBe("misconfigured");
  });
  it("a partial key pair is misconfigured, never a silent pass-through", () => {
    for (const flag of [undefined, "1"]) {
      expect(resolveSetupPhase("pk_x", undefined, flag)).toBe("misconfigured");
      expect(resolveSetupPhase(undefined, "sk_x", flag)).toBe("misconfigured");
      expect(isSetupMode("pk_x", undefined, flag)).toBe(false);
      expect(isAuthMisconfigured("pk_x", undefined, flag)).toBe(true);
      expect(isAuthMisconfigured(" ", "sk_x", flag)).toBe(true);
    }
  });
});

describe("setup token test reset", () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllEnvs();
    __resetSetupTokenForTests();
  });

  it("clears issuedAt so an expired window does not leak into the next test", () => {
    vi.stubEnv("RADON_SETUP_TOKEN", "env-token-value");
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-11-01T00:00:00Z"));
    __resetSetupTokenForTests();
    getSetupToken();
    vi.setSystemTime(Date.now() + SETUP_TOKEN_TTL_MS + 1);
    expect(verifySetupToken("env-token-value")).toBe(false);

    __resetSetupTokenForTests();
    expect(verifySetupToken("env-token-value")).toBe(true);
  });
});
