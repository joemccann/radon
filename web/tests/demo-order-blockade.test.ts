import { describe, it, expect, vi } from "vitest";
import { resolveDemoOrderDecision } from "@/lib/demo/orderBlockade";

const NOW = Date.parse("2026-06-25T12:00:00-04:00");
const FUTURE = "2026-06-29T16:00:00-04:00";
const PAST = "2026-06-24T16:00:00-04:00";

const auth = (metadata: unknown) =>
  vi.fn().mockResolvedValue({ sessionClaims: { metadata } });

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
