import { describe, it, expect, vi } from "vitest";
import { enforceDemoAiQuota } from "@/lib/demo/enforceAiQuota";
import type { QuotaDbClient } from "@/lib/demo/aiQuota";

const NOW = new Date("2026-06-25T12:00:00-04:00");
const FUTURE = "2026-06-29T16:00:00-04:00";
const PAST = "2026-06-24T16:00:00-04:00";

function fakeQuotaDb(initial = 0): QuotaDbClient {
  let count = initial;
  return {
    async execute({ sql }) {
      if (sql.includes("INSERT INTO demo_ai_usage")) {
        count += 1;
        return { rows: [] };
      }
      return { rows: [{ call_count: count }] };
    },
  };
}

const authFor = (metadata: unknown, userId: string | null = "u") =>
  vi.fn().mockResolvedValue({ userId, sessionClaims: { metadata } });

describe("enforceDemoAiQuota", () => {
  it("non-demo user → null (no quota, demo DB never touched)", async () => {
    const dbFactory = vi.fn();
    const res = await enforceDemoAiQuota("assistant", {
      authFn: authFor(null),
      dbFactory,
      now: NOW,
    });
    expect(res).toBeNull();
    expect(dbFactory).not.toHaveBeenCalled();
  });

  it("expired demo user → 403", async () => {
    const res = await enforceDemoAiQuota("assistant", {
      authFn: authFor({ demoRole: "trial", demoTrialExpiresAt: PAST }),
      dbFactory: () => fakeQuotaDb(),
      now: NOW,
    });
    expect(res?.status).toBe(403);
  });

  it("active demo user under quota → null", async () => {
    const res = await enforceDemoAiQuota("assistant", {
      authFn: authFor({ demoRole: "trial", demoTrialExpiresAt: FUTURE }),
      dbFactory: () => fakeQuotaDb(0),
      now: NOW,
    });
    expect(res).toBeNull();
  });

  it("active demo user over quota → 429 with reset", async () => {
    // assistant limit = 5; start already at 5 so the next increment → 6 > 5.
    const res = await enforceDemoAiQuota("assistant", {
      authFn: authFor({ demoRole: "trial", demoTrialExpiresAt: FUTURE }),
      dbFactory: () => fakeQuotaDb(5),
      now: NOW,
    });
    expect(res?.status).toBe(429);
    const body = await res!.json();
    expect(body.code).toBe("DEMO_AI_QUOTA_EXCEEDED");
    expect(body.limit).toBe(5);
  });
});
