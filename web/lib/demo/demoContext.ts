// Pure demo-trial context resolution — client-safe module.
//
// Split from demoRole.ts because that file's `getDemoContext` reaches for
// `@clerk/nextjs/server`, which Turbopack refuses to bundle into a Client
// Component graph ('server-only' import). Client code (DemoWelcomeModal)
// imports the pure resolver from HERE; server/edge code keeps importing from
// demoRole.ts, which re-exports everything below.
//
// Edge-safe: NO `node:*` imports (the middleware expiry gate consumes this).

export type DemoRole = "trial" | (string & {});

export type DemoPublicMetadata = {
  demoRole?: DemoRole;
  // ISO-8601 ET timestamps, written at signup by the user.created webhook.
  demoTrialStartedAt?: string;
  demoTrialExpiresAt?: string;
};

export type DemoContext = {
  isDemo: boolean;
  demoRole: DemoRole | null;
  trialStartedAt: string | null;
  trialExpiresAt: string | null;
  /** Access denied: a lapsed trial OR a pending one (see `pending`). */
  expired: boolean;
  /** Provisioning not finished: demoRole "pending" or no parseable expiry. */
  pending: boolean;
};

function parseMs(value: unknown): number | null {
  if (typeof value !== "string" || value.length === 0) return null;
  const ms = Date.parse(value);
  return Number.isFinite(ms) ? ms : null;
}

/**
 * Role allowlist. A role string outside this list is never access: it resolves
 * to `invalid` (denied) instead of riding a future expiry into an active trial.
 */
export const KNOWN_DEMO_ROLES = ["trial", "pending"] as const;

/**
 * The one trial state machine, derivable from EITHER store: Clerk metadata
 * (`resolveTrialState`) or the demo_users row (`trialStateFromRow`). Only
 * `active` grants access.
 *
 *   pending  provisioning not finished (role "pending", or no parseable expiry)
 *   active   known role, parseable expiry in the future
 *   lapsed   known role, expiry reached (an operator revoke stamps expiry=now)
 *   revoked  row-only: the operator revoked the trial
 *   invalid  unknown role / row status: fail closed
 */
export type TrialState =
  | { kind: "pending" }
  | { kind: "active"; startedAt: string | null; expiresAt: string }
  | { kind: "lapsed"; startedAt: string | null; expiresAt: string }
  | { kind: "revoked" }
  | { kind: "invalid"; reason: string };

function clockState(
  startedAt: string | null,
  expiresAt: string | null,
  now: number,
): TrialState {
  const expiresMs = parseMs(expiresAt);
  if (expiresMs === null) return { kind: "pending" };
  return now >= expiresMs
    ? { kind: "lapsed", startedAt, expiresAt: expiresAt as string }
    : { kind: "active", startedAt, expiresAt: expiresAt as string };
}

/** Clerk-side state. `null` = not a demo identity (no `demoRole`). */
export function resolveTrialState(
  metadata: DemoPublicMetadata | null | undefined,
  now: number = Date.now(),
): TrialState | null {
  const demoRole = metadata?.demoRole ?? null;
  if (!demoRole) return null;
  if (!(KNOWN_DEMO_ROLES as readonly string[]).includes(demoRole)) {
    return { kind: "invalid", reason: "unknown demo role" };
  }
  if (demoRole === "pending") return { kind: "pending" };
  return clockState(
    metadata?.demoTrialStartedAt ?? null,
    metadata?.demoTrialExpiresAt ?? null,
    now,
  );
}

/** DB-side state. `null` = no demo_users row (never provisioned). */
export function trialStateFromRow(
  row: { status: string; started_at: string | null; expires_at: string | null } | null,
  now: number = Date.now(),
): TrialState | null {
  if (!row) return null;
  if (row.status === "revoked") return { kind: "revoked" };
  if (row.status === "expired") {
    return row.expires_at
      ? { kind: "lapsed", startedAt: row.started_at, expiresAt: row.expires_at }
      : { kind: "invalid", reason: "expired row without expiry" };
  }
  if (row.status !== "active") return { kind: "invalid", reason: "unknown row status" };
  return clockState(row.started_at, row.expires_at, now);
}

/**
 * Pure resolver over a metadata object. Returns `null` when the user is NOT a
 * demo user (no `demoRole`) — default-deny: absence of the role means "not a
 * trial", so non-demo callers get `null` and skip every demo gate.
 *
 * Derived from `resolveTrialState`: `pending` routes a half-provisioned signup
 * to /demo-pending; `expired` (access denied) is every state except `active`.
 */
export function resolveDemoContext(
  metadata: DemoPublicMetadata | null | undefined,
  now: number = Date.now(),
): DemoContext | null {
  const state = resolveTrialState(metadata, now);
  if (!state) return null;
  return {
    isDemo: true,
    demoRole: metadata?.demoRole ?? null,
    trialStartedAt: metadata?.demoTrialStartedAt ?? null,
    trialExpiresAt: metadata?.demoTrialExpiresAt ?? null,
    expired: state.kind !== "active",
    pending: state.kind === "pending",
  };
}
