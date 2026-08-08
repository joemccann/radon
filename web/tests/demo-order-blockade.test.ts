import { describe, it, expect, vi } from "vitest";
import {
  demoOrderRefusal,
  isFailClosedEnvironment,
  resolveDemoOrderDecision,
} from "@/lib/demo/orderBlockade";

const NOW = Date.parse("2026-06-25T12:00:00-04:00");
const FUTURE = "2026-06-29T16:00:00-04:00";
const PAST = "2026-06-24T16:00:00-04:00";

const auth = (metadata: unknown) =>
  vi.fn().mockResolvedValue({ sessionClaims: { metadata } });

const throwingAuth = () =>
  vi.fn().mockRejectedValue(new Error("clerkMiddleware() was not run"));

describe("resolveDemoOrderDecision", () => {
  it("non-demo user → allow (real IB path)", async () => {
    const d = await resolveDemoOrderDecision({ authFn: auth(null), now: NOW });
    expect(d.action).toBe("allow");
  });

  it("active demo user → paper", async () => {
    const d = await resolveDemoOrderDecision({
      authFn: auth({ demoRole: "trial", demoTrialExpiresAt: FUTURE }),
      now: NOW,
    });
    expect(d.action).toBe("paper");
  });

  it("expired demo user → block-expired", async () => {
    const d = await resolveDemoOrderDecision({
      authFn: auth({ demoRole: "trial", demoTrialExpiresAt: PAST }),
      now: NOW,
    });
    expect(d.action).toBe("block-expired");
  });
});

describe("resolveDemoOrderDecision — fail-closed on auth error (T-018)", () => {
  it("auth throwing in PRODUCTION is never 'allow'", async () => {
    const d = await resolveDemoOrderDecision({
      authFn: throwingAuth(),
      now: NOW,
      nodeEnv: "production",
    });
    // The regression: a Clerk hiccup used to hand a demo user the real IB path.
    expect(d.action).not.toBe("allow");
    expect(d.action).toBe("auth-unavailable");
  });

  it("auth throwing outside production stays 'allow' (Clerk is not enforced there)", async () => {
    for (const nodeEnv of ["test", "development"]) {
      const d = await resolveDemoOrderDecision({
        authFn: throwingAuth(),
        now: NOW,
        nodeEnv,
      });
      expect(d.action, nodeEnv).toBe("allow");
    }
  });

  it("isFailClosedEnvironment is production-only", () => {
    expect(isFailClosedEnvironment("production")).toBe(true);
    expect(isFailClosedEnvironment("test")).toBe(false);
    expect(isFailClosedEnvironment("development")).toBe(false);
    expect(isFailClosedEnvironment(undefined)).toBe(false);
  });
});

describe("demoOrderRefusal — modify/cancel have no paper equivalent", () => {
  it("allow → no refusal (live path proceeds)", () => {
    expect(demoOrderRefusal({ action: "allow" }, "modify")).toBeNull();
    expect(demoOrderRefusal({ action: "allow" }, "cancel")).toBeNull();
  });

  it("active demo → 403, not a paper redirect", () => {
    for (const action of ["modify", "cancel"] as const) {
      const refusal = demoOrderRefusal({ action: "paper" }, action);
      expect(refusal?.status, action).toBe(403);
      expect(refusal?.code, action).toBe("DEMO_ORDER_BLOCKED");
      expect(refusal?.message, action).toContain(`cannot ${action} live orders`);
    }
  });

  it("expired demo → 403", () => {
    const refusal = demoOrderRefusal(
      { action: "block-expired", trialExpiresAt: PAST },
      "cancel",
    );
    expect(refusal?.status).toBe(403);
    expect(refusal?.code).toBe("DEMO_TRIAL_EXPIRED");
    expect(refusal?.message).toBe("Demo trial expired. Order not cancelled.");
  });

  it("auth-unavailable → 503, and says so", () => {
    const refusal = demoOrderRefusal({ action: "auth-unavailable" }, "modify");
    expect(refusal?.status).toBe(503);
    expect(refusal?.code).toBe("AUTH_UNAVAILABLE");
    expect(refusal?.message).toContain("Order not modified");
  });

  it("copy carries no em dashes", () => {
    const messages = (["modify", "cancel"] as const).flatMap((action) =>
      (
        [
          { action: "paper" },
          { action: "block-expired", trialExpiresAt: PAST },
          { action: "auth-unavailable" },
        ] as const
      ).map((decision) => demoOrderRefusal(decision, action)?.message ?? ""),
    );
    for (const message of messages) expect(message).not.toContain("—");
  });
});
