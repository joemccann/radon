import { describe, it, expect, vi } from "vitest";
import { requireDemoAdmin } from "@/lib/demo/adminAuth";
import {
  listDemoUsersWithUsage,
  revokeTrial,
  extendTrial,
} from "@/lib/demo/adminActions";
import { upsertDemoUser, getDemoUser } from "@/lib/demo/demoUsers";
import { makeFakeDemoDb } from "./helpers/fakeDemoDb";

const NOW = new Date("2026-06-25T12:00:00-04:00");
const EXPIRY = {
  startedAt: "2026-06-25T12:00:00-04:00",
  expiresAt: "2026-06-29T16:00:00-04:00",
  tradingDays: 3,
  active: true,
};

async function seeded() {
  const db = makeFakeDemoDb();
  await upsertDemoUser({
    db,
    userId: "user_1",
    email: "a@demo.test",
    demoRole: "trial",
    startedAt: "2026-06-24T09:30:00-04:00",
    expiresAt: "2026-06-26T16:00:00-04:00",
    now: new Date("2026-06-24T09:30:00-04:00"),
  });
  return db;
}

describe("requireDemoAdmin", () => {
  it("allows an allowlisted operator", async () => {
    const id = await requireDemoAdmin({
      authFn: async () => ({ userId: "op_1" }),
      allowedRaw: "op_1, op_2",
    });
    expect(id).toBe("op_1");
  });
  it("rejects a non-allowlisted user", async () => {
    const id = await requireDemoAdmin({
      authFn: async () => ({ userId: "trial_x" }),
      allowedRaw: "op_1",
    });
    expect(id).toBeNull();
  });
  it("default-deny when ALLOWED_USER_IDS is empty", async () => {
    const id = await requireDemoAdmin({
      authFn: async () => ({ userId: "op_1" }),
      allowedRaw: "",
    });
    expect(id).toBeNull();
  });
});

describe("admin actions", () => {
  it("listDemoUsersWithUsage attaches today's burn", async () => {
    const db = await seeded();
    db.setUsage("user_1", "assistant", "2026-06-25", 4);
    const rows = await listDemoUsersWithUsage(db, NOW);
    expect(rows).toHaveLength(1);
    expect(rows[0].aiUsage).toEqual({ assistant: 4 });
  });

  it("revokeTrial expires both stores", async () => {
    const db = await seeded();
    const setClerkMetadata = vi.fn().mockResolvedValue(undefined);
    const res = await revokeTrial("user_1", {
      db,
      setClerkMetadata,
      computeExpiry: vi.fn(),
      now: NOW,
    });
    expect(res.ok).toBe(true);
    const row = await getDemoUser(db, "user_1");
    expect(row?.status).toBe("revoked");
    // Clerk expiry stamped at NOW (immediately expired).
    expect(setClerkMetadata).toHaveBeenCalledWith(
      "user_1",
      expect.objectContaining({ demoTrialExpiresAt: NOW.toISOString() }),
    );
  });

  it("revokeTrial on an unknown user fails", async () => {
    const db = makeFakeDemoDb();
    const res = await revokeTrial("ghost", {
      db,
      setClerkMetadata: vi.fn(),
      computeExpiry: vi.fn(),
      now: NOW,
    });
    expect(res.ok).toBe(false);
  });

  it("extendTrial pushes expiry out in both stores", async () => {
    const db = await seeded();
    const setClerkMetadata = vi.fn().mockResolvedValue(undefined);
    const res = await extendTrial("user_1", 3, {
      db,
      setClerkMetadata,
      computeExpiry: vi.fn().mockResolvedValue(EXPIRY),
      now: NOW,
    });
    expect(res).toEqual({ ok: true, expiresAt: EXPIRY.expiresAt });
    const row = await getDemoUser(db, "user_1");
    expect(row?.expires_at).toBe(EXPIRY.expiresAt);
    expect(row?.status).toBe("active");
    expect(setClerkMetadata).toHaveBeenCalledWith(
      "user_1",
      expect.objectContaining({ demoTrialExpiresAt: EXPIRY.expiresAt }),
    );
  });

  it("extendTrial rejects a non-positive day count", async () => {
    const db = await seeded();
    const res = await extendTrial("user_1", 0, {
      db,
      setClerkMetadata: vi.fn(),
      computeExpiry: vi.fn(),
      now: NOW,
    });
    expect(res.ok).toBe(false);
  });
});
