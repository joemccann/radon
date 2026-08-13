import { describe, it, expect, vi } from "vitest";
import {
  provisionDemoTrial,
  pickPrimaryEmail,
  parseAllowedUserIds,
  type ClerkUserCreatedData,
} from "@/lib/demo/provisionTrial";
import { getDemoUser } from "@/lib/demo/demoUsers";
import { makeFakeDemoDb } from "./helpers/fakeDemoDb";

const EXPIRY = {
  startedAt: "2026-06-25T09:30:00-04:00",
  expiresAt: "2026-06-29T16:00:00-04:00",
  tradingDays: 3,
  active: true,
};

function deps(overrides = {}) {
  return {
    db: makeFakeDemoDb(),
    computeExpiry: vi.fn().mockResolvedValue(EXPIRY),
    setClerkMetadata: vi.fn().mockResolvedValue(undefined),
    now: new Date("2026-06-25T09:30:00-04:00"),
    ...overrides,
  };
}

function demoUser(over: Partial<ClerkUserCreatedData> = {}): ClerkUserCreatedData {
  return {
    id: "user_demo",
    email_addresses: [{ id: "em_1", email_address: "trial@demo.test" }],
    primary_email_address_id: "em_1",
    unsafe_metadata: { demo: true },
    ...over,
  };
}

describe("provisionDemoTrial", () => {
  it("provisions a marked demo signup — DB row + Clerk metadata", async () => {
    const d = deps();
    const result = await provisionDemoTrial(demoUser(), d);
    expect(result.provisioned).toBe(true);
    expect(result.expiresAt).toBe(EXPIRY.expiresAt);

    const row = await getDemoUser(d.db, "user_demo");
    expect(row?.email).toBe("trial@demo.test");
    expect(row?.demo_role).toBe("trial");

    expect(d.setClerkMetadata).toHaveBeenCalledWith("user_demo", {
      demoRole: "trial",
      demoTrialStartedAt: EXPIRY.startedAt,
      demoTrialExpiresAt: EXPIRY.expiresAt,
    });
  });

  it("NEVER provisions an operator (allowlisted) even if marked demo", async () => {
    const d = deps({ allowedUserIds: new Set(["user_demo"]) });
    const result = await provisionDemoTrial(demoUser(), d);
    expect(result.provisioned).toBe(false);
    expect(d.setClerkMetadata).not.toHaveBeenCalled();
    expect(d.db.users.size).toBe(0);
  });

  it("skips a signup without the demo marker", async () => {
    const d = deps();
    const result = await provisionDemoTrial(demoUser({ unsafe_metadata: {} }), d);
    expect(result.provisioned).toBe(false);
    expect(d.computeExpiry).not.toHaveBeenCalled();
  });

  it("assumeDemo provisions an UNMARKED signup (OAuth via the sign-in page)", async () => {
    const d = deps({ assumeDemo: true });
    const result = await provisionDemoTrial(demoUser({ unsafe_metadata: {} }), d);
    expect(result.provisioned).toBe(true);
    expect(d.setClerkMetadata).toHaveBeenCalled();
  });

  it("assumeDemo still NEVER provisions an operator", async () => {
    const d = deps({ assumeDemo: true, allowedUserIds: new Set(["user_demo"]) });
    const result = await provisionDemoTrial(demoUser({ unsafe_metadata: {} }), d);
    expect(result.provisioned).toBe(false);
    expect(d.setClerkMetadata).not.toHaveBeenCalled();
  });

  it("writes denied Clerk metadata before slow provisioning, then activates after the DB row", async () => {
    const order: string[] = [];
    const db = makeFakeDemoDb();
    const origExecute = db.execute.bind(db);
    db.execute = async (q) => {
      if (q.sql.includes("INSERT INTO demo_users")) order.push("db");
      return origExecute(q);
    };
    const setClerkMetadata = vi.fn(async () => {
      order.push("clerk");
    });
    await provisionDemoTrial(demoUser(), {
      db,
      computeExpiry: vi.fn().mockResolvedValue(EXPIRY),
      setClerkMetadata,
      now: new Date(),
    });
    expect(order).toEqual(["clerk", "db", "clerk"]);
    expect(setClerkMetadata.mock.calls[0]).toEqual(["user_demo", { demoRole: "pending" }]);
  });

  it("leaves a failed provisioning attempt explicitly pending in Clerk", async () => {
    const setClerkMetadata = vi.fn().mockResolvedValue(undefined);
    await expect(provisionDemoTrial(demoUser(), deps({
      setClerkMetadata,
      computeExpiry: vi.fn().mockRejectedValue(new Error("calendar down")),
    }))).rejects.toThrow("calendar down");
    expect(setClerkMetadata).toHaveBeenCalledTimes(1);
    expect(setClerkMetadata).toHaveBeenCalledWith("user_demo", { demoRole: "pending" });
  });
});

describe("pickPrimaryEmail", () => {
  it("prefers the primary address", () => {
    expect(
      pickPrimaryEmail({
        id: "u",
        email_addresses: [
          { id: "a", email_address: "alt@x.test" },
          { id: "b", email_address: "primary@x.test" },
        ],
        primary_email_address_id: "b",
      }),
    ).toBe("primary@x.test");
  });
  it("falls back to the first when no primary id matches", () => {
    expect(
      pickPrimaryEmail({ id: "u", email_addresses: [{ id: "a", email_address: "only@x.test" }] }),
    ).toBe("only@x.test");
  });
  it("null when no emails", () => {
    expect(pickPrimaryEmail({ id: "u", email_addresses: [] })).toBeNull();
  });
});

describe("parseAllowedUserIds", () => {
  it("splits, trims, drops blanks", () => {
    const set = parseAllowedUserIds(" user_a , user_b ,, ");
    expect([...set]).toEqual(["user_a", "user_b"]);
  });
  it("empty/undefined → empty set", () => {
    expect(parseAllowedUserIds(undefined).size).toBe(0);
  });
});
