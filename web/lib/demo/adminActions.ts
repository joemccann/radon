// Operator actions for the demo-users admin panel (Phase 6).
//
// REVOKE and EXTEND must keep the two sources of truth in lock-step: the demo
// Turso `demo_users` row AND the Clerk publicMetadata the middleware reads. A
// row-only change wouldn't actually gate access; a metadata-only change would
// drift the operator panel. Both are written here. Side effects are injected so
// the orchestration is unit-testable.

import {
  listDemoUsers,
  getDemoUser,
  revokeDemoUser,
  extendDemoUser,
  getDemoUserAiUsage,
  type DemoDbClient,
  type DemoUserRow,
} from "./demoUsers";
import type { DemoPublicMetadata } from "./demoRole";
import type { TrialExpiry } from "./trialExpiry";
import { etDayKey } from "./aiQuota";

export type AdminActionDeps = {
  db: DemoDbClient;
  setClerkMetadata: (userId: string, meta: DemoPublicMetadata) => Promise<void>;
  computeExpiry: (startIsoEt: string, tradingDays: number) => Promise<TrialExpiry>;
  now?: Date;
};

export type DemoUserWithUsage = DemoUserRow & {
  aiUsage: Record<string, number>;
};

/** List every trial with today's (ET) per-endpoint AI burn attached. */
export async function listDemoUsersWithUsage(
  db: DemoDbClient,
  now: Date = new Date(),
): Promise<DemoUserWithUsage[]> {
  const day = etDayKey(now);
  const rows = await listDemoUsers(db);
  const out: DemoUserWithUsage[] = [];
  for (const row of rows) {
    out.push({ ...row, aiUsage: await getDemoUserAiUsage(db, row.user_id, day) });
  }
  return out;
}

/** REVOKE — expire the trial immediately in BOTH stores. */
export async function revokeTrial(
  userId: string,
  deps: AdminActionDeps,
): Promise<{ ok: true } | { ok: false; reason: string }> {
  const now = deps.now ?? new Date();
  const existing = await getDemoUser(deps.db, userId);
  if (!existing) return { ok: false, reason: "no such demo user" };

  await revokeDemoUser({ db: deps.db, userId, now });
  // Expire in Clerk by stamping the expiry at "now" — middleware treats any
  // demoRole user whose expiry is in the past as blocked.
  await deps.setClerkMetadata(userId, {
    demoRole: "trial",
    demoTrialStartedAt: existing.started_at ?? undefined,
    demoTrialExpiresAt: now.toISOString(),
  });
  return { ok: true };
}

/** EXTEND — grant N more trading days from now, in BOTH stores. */
export async function extendTrial(
  userId: string,
  tradingDays: number,
  deps: AdminActionDeps,
): Promise<{ ok: true; expiresAt: string } | { ok: false; reason: string }> {
  if (!Number.isInteger(tradingDays) || tradingDays < 1) {
    return { ok: false, reason: "tradingDays must be a positive integer" };
  }
  const now = deps.now ?? new Date();
  const existing = await getDemoUser(deps.db, userId);
  if (!existing) return { ok: false, reason: "no such demo user" };

  const expiry = await deps.computeExpiry(now.toISOString(), tradingDays);
  await extendDemoUser({ db: deps.db, userId, expiresAt: expiry.expiresAt });
  await deps.setClerkMetadata(userId, {
    demoRole: "trial",
    demoTrialStartedAt: existing.started_at ?? expiry.startedAt,
    demoTrialExpiresAt: expiry.expiresAt,
  });
  return { ok: true, expiresAt: expiry.expiresAt };
}
