// Demo trial state machine (audit 2026-11-03 §1 #18).
//
// One TrialState union derived from either store (Clerk metadata, demo_users
// row), one write order for operator actions (DB first as source of truth,
// then Clerk, with a compensating DB restore when Clerk fails), an allowlist
// of role strings, and a webhook ledger that cannot strand a user pending.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  resolveDemoContext,
  resolveTrialState,
  trialStateFromRow,
} from "@/lib/demo/demoContext";
import { revokeTrial, extendTrial } from "@/lib/demo/adminActions";
import { provisionDemoTrial } from "@/lib/demo/provisionTrial";
import { getDemoUser, upsertDemoUser } from "@/lib/demo/demoUsers";
import { makeFakeDemoDb, type FakeDemoDb } from "./helpers/fakeDemoDb";

const NOW = new Date("2026-06-25T12:00:00-04:00");
const FUTURE = "2026-06-29T16:00:00-04:00";
const PAST = "2026-06-24T16:00:00-04:00";

async function seeded(expiresAt = "2026-06-26T16:00:00-04:00"): Promise<FakeDemoDb> {
  const db = makeFakeDemoDb();
  await upsertDemoUser({
    db,
    userId: "user_1",
    email: "a@demo.test",
    demoRole: "trial",
    startedAt: "2026-06-24T09:30:00-04:00",
    expiresAt,
    now: new Date("2026-06-24T09:30:00-04:00"),
  });
  return db;
}

describe("TrialState from Clerk metadata", () => {
  const now = NOW.getTime();

  it("allowlists role strings: an unknown role with a future expiry is never active", () => {
    const state = resolveTrialState({ demoRole: "admin", demoTrialExpiresAt: FUTURE }, now);
    expect(state).toEqual({ kind: "invalid", reason: "unknown demo role" });
    const ctx = resolveDemoContext({ demoRole: "admin", demoTrialExpiresAt: FUTURE }, now);
    expect(ctx!.expired).toBe(true);
    expect(ctx!.pending).toBe(false);
  });

  it("maps the known roles onto pending / active / lapsed", () => {
    expect(resolveTrialState({}, now)).toBeNull();
    expect(resolveTrialState({ demoRole: "pending", demoTrialExpiresAt: FUTURE }, now))
      .toEqual({ kind: "pending" });
    expect(resolveTrialState({ demoRole: "trial" }, now)).toEqual({ kind: "pending" });
    expect(resolveTrialState({ demoRole: "trial", demoTrialExpiresAt: FUTURE }, now))
      .toEqual({ kind: "active", startedAt: null, expiresAt: FUTURE });
    expect(resolveTrialState({ demoRole: "trial", demoTrialExpiresAt: PAST }, now))
      .toEqual({ kind: "lapsed", startedAt: null, expiresAt: PAST });
  });
});

describe("TrialState from the demo_users row", () => {
  const base = { started_at: "2026-06-24T09:30:00-04:00", expires_at: FUTURE };
  const now = NOW.getTime();
  it("derives every kind", () => {
    expect(trialStateFromRow(null, now)).toBeNull();
    expect(trialStateFromRow({ ...base, status: "revoked" }, now)).toEqual({ kind: "revoked" });
    expect(trialStateFromRow({ ...base, status: "active" }, now))
      .toEqual({ kind: "active", startedAt: base.started_at, expiresAt: FUTURE });
    expect(trialStateFromRow({ ...base, expires_at: PAST, status: "active" }, now))
      .toEqual({ kind: "lapsed", startedAt: base.started_at, expiresAt: PAST });
    expect(trialStateFromRow({ ...base, status: "expired" }, now))
      .toEqual({ kind: "lapsed", startedAt: base.started_at, expiresAt: FUTURE });
    expect(trialStateFromRow({ ...base, expires_at: null, status: "active" }, now))
      .toEqual({ kind: "pending" });
    expect(trialStateFromRow({ ...base, status: "bogus" as never }, now))
      .toEqual({ kind: "invalid", reason: "unknown row status" });
  });
});

describe("operator actions write DB first, then Clerk", () => {
  it("revoke: a failed DB write never revokes Clerk alone (no replay can resurrect it)", async () => {
    const db = await seeded();
    const orig = db.execute.bind(db);
    db.execute = async (q) => {
      if (q.sql.includes("status = 'revoked'")) throw new Error("turso down");
      return orig(q);
    };
    const setClerkMetadata = vi.fn().mockResolvedValue(undefined);
    await expect(
      revokeTrial("user_1", { db, setClerkMetadata, computeExpiry: vi.fn(), now: NOW }),
    ).rejects.toThrow("turso down");
    expect(setClerkMetadata).not.toHaveBeenCalled();
  });

  it("revoke: DB row is committed before the Clerk write", async () => {
    const db = await seeded();
    const order: string[] = [];
    const orig = db.execute.bind(db);
    db.execute = async (q) => {
      if (q.sql.includes("UPDATE demo_users")) order.push("db");
      return orig(q);
    };
    const setClerkMetadata = vi.fn(async () => {
      order.push("clerk");
    });
    await revokeTrial("user_1", { db, setClerkMetadata, computeExpiry: vi.fn(), now: NOW });
    expect(order).toEqual(["db", "clerk"]);
  });

  it("revoke: a Clerk failure restores the prior DB row (stores stay consistent)", async () => {
    const db = await seeded();
    await expect(
      revokeTrial("user_1", {
        db,
        setClerkMetadata: vi.fn().mockRejectedValue(new Error("Clerk unavailable")),
        computeExpiry: vi.fn(),
        now: NOW,
      }),
    ).rejects.toThrow("Clerk unavailable");
    const row = await getDemoUser(db, "user_1");
    expect(row?.status).toBe("active");
    expect(row?.revoked_at).toBeNull();
  });

  it("extend: a Clerk failure restores the prior expiry and status", async () => {
    const db = await seeded(PAST);
    await (await import("@/lib/demo/demoUsers")).revokeDemoUser({
      db,
      userId: "user_1",
      now: new Date("2026-06-24T10:00:00-04:00"),
    });
    const before = await getDemoUser(db, "user_1");
    await expect(
      extendTrial("user_1", 3, {
        db,
        setClerkMetadata: vi.fn().mockRejectedValue(new Error("Clerk unavailable")),
        computeExpiry: vi.fn().mockResolvedValue({
          startedAt: NOW.toISOString(),
          expiresAt: FUTURE,
          tradingDays: 3,
          active: true,
        }),
        now: NOW,
      }),
    ).rejects.toThrow("Clerk unavailable");
    const after = await getDemoUser(db, "user_1");
    expect(after?.status).toBe(before?.status);
    expect(after?.expires_at).toBe(before?.expires_at);
    expect(after?.revoked_at).toBe(before?.revoked_at);
  });
});

describe("provisioning stamps Clerk from the committed DB row", () => {
  const user = { id: "user_1", unsafe_metadata: { demo: true } };
  const expiry = { startedAt: NOW.toISOString(), expiresAt: FUTURE, tradingDays: 2, active: true };

  it("a row revoked while provisioning ran is never stamped active", async () => {
    const db = makeFakeDemoDb();
    const orig = db.execute.bind(db);
    db.execute = async (q) => {
      const out = await orig(q);
      if (q.sql.includes("INSERT INTO demo_users")) {
        const row = db.users.get("user_1")!;
        db.users.set("user_1", { ...row, status: "revoked", revoked_at: NOW.toISOString() });
      }
      return out;
    };
    const setClerkMetadata = vi.fn().mockResolvedValue(undefined);
    const res = await provisionDemoTrial(user, {
      db,
      computeExpiry: vi.fn().mockResolvedValue(expiry),
      setClerkMetadata,
      now: NOW,
    });
    expect(res.provisioned).toBe(false);
    expect(setClerkMetadata.mock.calls.map((c) => c[1].demoRole)).toEqual(["pending"]);
  });

  it("an earlier concurrent row wins: Clerk mirrors the DB expiry, not the local computation", async () => {
    const db = makeFakeDemoDb();
    db.users.set("user_1", {
      user_id: "user_1",
      email: null,
      demo_role: "trial",
      started_at: null,
      expires_at: null,
      status: "active",
      revoked_at: null,
      created_at: NOW.toISOString(),
    });
    const orig = db.execute.bind(db);
    db.execute = async (q) => {
      if (q.sql.includes("INSERT INTO demo_users")) {
        // A concurrent delivery committed its window first.
        const row = db.users.get("user_1")!;
        db.users.set("user_1", { ...row, started_at: "2026-06-25T11:59:00-04:00", expires_at: "2026-06-29T16:00:00-04:00" });
      }
      return orig(q);
    };
    const setClerkMetadata = vi.fn().mockResolvedValue(undefined);
    await provisionDemoTrial(user, {
      db,
      computeExpiry: vi.fn().mockResolvedValue({ ...expiry, expiresAt: "2026-06-30T16:00:00-04:00" }),
      setClerkMetadata,
      now: NOW,
    });
    expect(setClerkMetadata).toHaveBeenLastCalledWith("user_1", {
      demoRole: "trial",
      demoTrialStartedAt: "2026-06-25T11:59:00-04:00",
      demoTrialExpiresAt: "2026-06-29T16:00:00-04:00",
    });
  });
});

// ── Webhook route: a stranded ledger claim must not leave a user pending ──
const fakeDb = { current: makeFakeDemoDb() };
const clerkUpdate = vi.fn();
const notify = vi.fn();
vi.mock("@/lib/db", () => ({ getDemoDb: () => fakeDb.current }));
vi.mock("@clerk/nextjs/server", () => ({
  clerkClient: async () => ({ users: { updateUserMetadata: clerkUpdate } }),
}));
vi.mock("@/lib/demo/trialExpiry", () => ({
  computeTrialExpiry: vi.fn(async (start: string) => ({
    startedAt: start,
    expiresAt: "2099-01-02T16:00:00-05:00",
    tradingDays: 2,
    active: true,
  })),
}));
vi.mock("@/lib/notify/pushover", () => ({
  notifyDemoProvisioningFailure: (...args: unknown[]) => notify(...args),
}));

describe("clerk webhook ledger", () => {
  const KEY_BYTES = new Uint8Array(Array.from({ length: 24 }, (_, i) => (i * 5 + 11) % 256));
  const SECRET = `whsec_${btoa(String.fromCharCode(...KEY_BYTES))}`;

  async function deliver(id: string, data: Record<string, unknown>): Promise<Response> {
    const { POST } = await import("@/app/api/webhooks/clerk/route");
    const body = JSON.stringify({ type: "user.created", data });
    const ts = String(Math.floor(Date.now() / 1000));
    const key = await crypto.subtle.importKey("raw", KEY_BYTES, { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
    const mac = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(`${id}.${ts}.${body}`));
    return POST(
      new Request("https://demo.radon.run/api/webhooks/clerk", {
        method: "POST",
        headers: {
          "svix-id": id,
          "svix-timestamp": ts,
          "svix-signature": `v1,${btoa(String.fromCharCode(...new Uint8Array(mac)))}`,
        },
        body,
      }),
    );
  }

  beforeEach(() => {
    vi.stubEnv("CLERK_WEBHOOK_SECRET", SECRET);
    fakeDb.current = makeFakeDemoDb();
    clerkUpdate.mockReset().mockResolvedValue(undefined);
    notify.mockReset();
  });
  afterEach(() => vi.unstubAllEnvs());

  it("a retry whose ledger row was never released still provisions an unprovisioned user", async () => {
    const db = fakeDb.current;
    const orig = db.execute.bind(db);
    let failInsert = true;
    db.execute = async (q) => {
      if (q.sql.includes("INSERT INTO demo_users") && failInsert) throw new Error("turso blip");
      if (q.sql.includes("DELETE FROM demo_webhook_events")) throw new Error("release failed");
      return orig(q);
    };
    const first = await deliver("msg_stranded", { id: "user_9", unsafe_metadata: { demo: true } });
    expect(first.status).toBe(500);
    expect(db.webhookEvents.has("msg_stranded")).toBe(true);
    expect(notify).toHaveBeenCalledWith(expect.stringContaining("ledger release failed"));

    failInsert = false;
    const retry = await deliver("msg_stranded", { id: "user_9", unsafe_metadata: { demo: true } });
    expect(retry.status).toBe(200);
    const json = await retry.json();
    expect(json.provisioned).toBe(true);
    expect((await getDemoUser(db, "user_9"))?.status).toBe("active");
    expect(clerkUpdate).toHaveBeenLastCalledWith("user_9", {
      publicMetadata: expect.objectContaining({ demoRole: "trial" }),
    });
  });

  it("a true duplicate (row committed) is acknowledged without re-running provisioning", async () => {
    const ok = await deliver("msg_dup", { id: "user_8", unsafe_metadata: { demo: true } });
    expect(ok.status).toBe(200);
    clerkUpdate.mockClear();
    const dup = await deliver("msg_dup", { id: "user_8", unsafe_metadata: { demo: true } });
    expect(await dup.json()).toEqual(expect.objectContaining({ duplicate: true }));
    expect(clerkUpdate).not.toHaveBeenCalled();
  });
});
