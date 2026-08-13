import { describe, it, expect } from "vitest";
import {
  claimDemoWebhookEvent,
  upsertDemoUser,
  listDemoUsers,
  getDemoUser,
  revokeDemoUser,
  extendDemoUser,
  getDemoUserAiUsage,
} from "@/lib/demo/demoUsers";
import { makeFakeDemoDb } from "./helpers/fakeDemoDb";

const T0 = new Date("2026-06-25T09:30:00-04:00");

async function seed(db = makeFakeDemoDb()) {
  await upsertDemoUser({
    db,
    userId: "user_1",
    email: "a@demo.test",
    demoRole: "trial",
    startedAt: "2026-06-25T09:30:00-04:00",
    expiresAt: "2026-06-27T16:00:00-04:00",
    now: T0,
  });
  return db;
}

describe("demoUsers accessor", () => {
  it("provision retry preserves the original trial clock", async () => {
    const db = await seed();
    expect(db.users.size).toBe(1);

    await upsertDemoUser({
      db,
      userId: "user_1",
      email: "a2@demo.test",
      demoRole: "trial",
      startedAt: "2026-06-25T10:00:00-04:00",
      expiresAt: "2026-06-29T16:00:00-04:00",
      now: T0,
    });
    expect(db.users.size).toBe(1);
    const row = await getDemoUser(db, "user_1");
    expect(row?.email).toBe("a2@demo.test");
    expect(row?.expires_at).toBe("2026-06-27T16:00:00-04:00");
    expect(row?.status).toBe("active");
  });

  it("provision retry cannot reactivate a revoked trial", async () => {
    const db = await seed();
    await revokeDemoUser({ db, userId: "user_1", now: T0 });
    await upsertDemoUser({
      db,
      userId: "user_1",
      email: "retry@demo.test",
      demoRole: "trial",
      startedAt: "2026-06-26T09:30:00-04:00",
      expiresAt: "2026-07-01T16:00:00-04:00",
      now: T0,
    });
    expect(await getDemoUser(db, "user_1")).toMatchObject({
      status: "revoked",
      expires_at: "2026-06-27T16:00:00-04:00",
    });
  });

  it("revoke flips status + stamps revoked_at", async () => {
    const db = await seed();
    await revokeDemoUser({ db, userId: "user_1", now: T0 });
    const row = await getDemoUser(db, "user_1");
    expect(row?.status).toBe("revoked");
    expect(row?.revoked_at).toBe(T0.toISOString());
  });

  it("extend updates expiry + reactivates", async () => {
    const db = await seed();
    await revokeDemoUser({ db, userId: "user_1", now: T0 });
    await extendDemoUser({ db, userId: "user_1", expiresAt: "2026-07-02T16:00:00-04:00" });
    const row = await getDemoUser(db, "user_1");
    expect(row?.status).toBe("active");
    expect(row?.revoked_at).toBeNull();
    expect(row?.expires_at).toBe("2026-07-02T16:00:00-04:00");
  });

  it("list returns newest first", async () => {
    const db = await seed();
    await upsertDemoUser({
      db,
      userId: "user_2",
      email: "b@demo.test",
      demoRole: "trial",
      startedAt: "2026-06-26T09:30:00-04:00",
      expiresAt: "2026-06-30T16:00:00-04:00",
      now: new Date("2026-06-26T09:30:00-04:00"),
    });
    const rows = await listDemoUsers(db);
    expect(rows.map((r) => r.user_id)).toEqual(["user_2", "user_1"]);
  });

  it("getDemoUserAiUsage returns per-endpoint counts for the day", async () => {
    const db = await seed();
    db.setUsage("user_1", "assistant", "2026-06-25", 3);
    db.setUsage("user_1", "ticker/info", "2026-06-25", 1);
    db.setUsage("user_1", "assistant", "2026-06-24", 9); // other day — excluded
    const usage = await getDemoUserAiUsage(db, "user_1", "2026-06-25");
    expect(usage).toEqual({ assistant: 3, "ticker/info": 1 });
  });

  it("claims a signed webhook event only once", async () => {
    const db = makeFakeDemoDb();
    await expect(claimDemoWebhookEvent(db, "msg_1", "user.created")).resolves.toBe(true);
    await expect(claimDemoWebhookEvent(db, "msg_1", "user.created")).resolves.toBe(false);
  });
});
