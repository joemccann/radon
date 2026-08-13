import { describe, it, expect } from "vitest";
import {
  resolveDemoContext,
  getDemoContext,
  type DemoPublicMetadata,
} from "@/lib/demo/demoRole";

const NOW = Date.parse("2026-06-18T12:00:00-04:00");

describe("resolveDemoContext", () => {
  it("returns an active context for a trial whose expiry is in the future", () => {
    const md: DemoPublicMetadata = {
      demoRole: "trial",
      demoTrialStartedAt: "2026-06-16T09:00:00-04:00",
      demoTrialExpiresAt: "2026-06-18T16:00:00-04:00", // after NOW
    };
    const ctx = resolveDemoContext(md, NOW);
    expect(ctx).not.toBeNull();
    expect(ctx!.isDemo).toBe(true);
    expect(ctx!.demoRole).toBe("trial");
    expect(ctx!.expired).toBe(false);
    expect(ctx!.trialExpiresAt).toBe("2026-06-18T16:00:00-04:00");
  });

  it("flags an expired trial when expiry is at/before now", () => {
    const md: DemoPublicMetadata = {
      demoRole: "trial",
      demoTrialStartedAt: "2026-06-12T09:00:00-04:00",
      demoTrialExpiresAt: "2026-06-17T16:00:00-04:00", // before NOW
    };
    const ctx = resolveDemoContext(md, NOW);
    expect(ctx).not.toBeNull();
    expect(ctx!.expired).toBe(true);
  });

  it("returns null for a non-demo user (no demoRole) — default-deny", () => {
    expect(resolveDemoContext({}, NOW)).toBeNull();
    expect(resolveDemoContext(null, NOW)).toBeNull();
    expect(resolveDemoContext(undefined, NOW)).toBeNull();
  });

  it("treats a missing/invalid expiry as expired (provisioning fails closed)", () => {
    const ctx = resolveDemoContext({ demoRole: "trial" }, NOW);
    expect(ctx).not.toBeNull();
    expect(ctx!.expired).toBe(true);
    expect(ctx!.trialExpiresAt).toBeNull();
  });
});

describe("getDemoContext (injected auth)", () => {
  it("reads metadata from sessionClaims.metadata", async () => {
    const authFn = async () => ({
      sessionClaims: {
        metadata: {
          demoRole: "trial",
          demoTrialExpiresAt: "2026-06-18T16:00:00-04:00",
        },
      },
    });
    const ctx = await getDemoContext(authFn, NOW);
    expect(ctx?.isDemo).toBe(true);
    expect(ctx?.expired).toBe(false);
  });

  it("falls back to publicMetadata when sessionClaims has no metadata", async () => {
    const authFn = async () => ({
      publicMetadata: {
        demoRole: "trial",
        demoTrialExpiresAt: "2026-06-17T16:00:00-04:00",
      },
    });
    const ctx = await getDemoContext(authFn, NOW);
    expect(ctx?.expired).toBe(true);
  });

  it("returns null for a signed-out / non-demo session", async () => {
    const authFn = async () => ({ sessionClaims: null });
    expect(await getDemoContext(authFn, NOW)).toBeNull();
  });
});
