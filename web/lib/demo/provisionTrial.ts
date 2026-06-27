// Demo trial provisioning, driven by the Clerk user.created webhook (Phase 2).
//
// Pure orchestration with fully injected side effects (DB, Clerk metadata
// write, expiry computation) so it is unit-testable with no live services.
//
// Same-instance design (decision 1): app.radon.run and demo.radon.run share
// one Clerk instance, so the webhook fires for EVERY signup. Two guards decide
// who becomes a trial:
//   1. NEVER provision an operator (ALLOWED_USER_IDS) — isolation guarantee 3:
//      a demo session must never carry an operator identity.
//   2. Only provision when the signup carries the demo marker
//      (unsafe_metadata.demo === true), set by the demo.radon.run sign-up page.

import { upsertDemoUser, type DemoDbClient } from "./demoUsers";
import type { DemoPublicMetadata } from "./demoRole";
import type { TrialExpiry } from "./trialExpiry";

export type ClerkUserCreatedData = {
  id: string;
  email_addresses?: Array<{ id: string; email_address: string }> | null;
  primary_email_address_id?: string | null;
  unsafe_metadata?: Record<string, unknown> | null;
};

export type ProvisionDeps = {
  db: DemoDbClient;
  computeExpiry: (startIsoEt: string) => Promise<TrialExpiry>;
  setClerkMetadata: (
    userId: string,
    publicMetadata: DemoPublicMetadata,
  ) => Promise<void>;
  allowedUserIds?: Set<string>;
  now?: Date;
};

export type ProvisionResult = {
  provisioned: boolean;
  reason?: string;
  expiresAt?: string;
};

export function pickPrimaryEmail(data: ClerkUserCreatedData): string | null {
  const emails = data.email_addresses ?? [];
  if (emails.length === 0) return null;
  const primary = emails.find((e) => e.id === data.primary_email_address_id);
  return (primary ?? emails[0]).email_address ?? null;
}

export function isDemoSignup(data: ClerkUserCreatedData): boolean {
  return data.unsafe_metadata?.demo === true;
}

export async function provisionDemoTrial(
  data: ClerkUserCreatedData,
  deps: ProvisionDeps,
): Promise<ProvisionResult> {
  const userId = data.id;
  if (!userId) return { provisioned: false, reason: "missing user id" };

  if (deps.allowedUserIds?.has(userId)) {
    return { provisioned: false, reason: "operator (allowlisted) — never a trial" };
  }
  if (!isDemoSignup(data)) {
    return { provisioned: false, reason: "not a demo signup (no demo marker)" };
  }

  const now = deps.now ?? new Date();
  const startIsoEt = now.toISOString();
  const expiry = await deps.computeExpiry(startIsoEt);
  const email = pickPrimaryEmail(data);

  // DB row FIRST so a user can never hold demo access (Clerk metadata) without
  // a tracking row. A Clerk-write failure then leaves no access + a dangling
  // row the sweep reconciles; a webhook retry is idempotent either way.
  await upsertDemoUser({
    db: deps.db,
    userId,
    email,
    demoRole: "trial",
    startedAt: expiry.startedAt,
    expiresAt: expiry.expiresAt,
    now,
  });

  await deps.setClerkMetadata(userId, {
    demoRole: "trial",
    demoTrialStartedAt: expiry.startedAt,
    demoTrialExpiresAt: expiry.expiresAt,
  });

  return { provisioned: true, expiresAt: expiry.expiresAt };
}

/** Parse the comma-separated ALLOWED_USER_IDS env into a set. */
export function parseAllowedUserIds(raw: string | undefined): Set<string> {
  return new Set(
    (raw ?? "")
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean),
  );
}
