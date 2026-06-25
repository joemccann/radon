// Demo trial context resolution (demo.radon.run).
//
// Reads demo status from Clerk publicMetadata. The pure resolver
// `resolveDemoContext` takes the metadata object directly so it is testable
// without a live Clerk session; `getDemoContext` is a thin server wrapper that
// pulls `sessionClaims.metadata` (or falls back to `publicMetadata`) via
// Clerk's `auth()`.
//
// Edge-safe: NO `node:*` imports. The Phase-2 middleware expiry gate imports
// from this module and runs in the Edge runtime (see
// feedback_middleware_edge_runtime).

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
  expired: boolean;
};

function parseMs(value: unknown): number | null {
  if (typeof value !== "string" || value.length === 0) return null;
  const ms = Date.parse(value);
  return Number.isFinite(ms) ? ms : null;
}

/**
 * Pure resolver over a metadata object. Returns `null` when the user is NOT a
 * demo user (no `demoRole`) — default-deny: absence of the role means "not a
 * trial", so non-demo callers get `null` and skip every demo gate.
 *
 * `expired` is true only for a demo user whose expiry timestamp is in the past
 * (relative to `now`, default: call time). A demo user with no/invalid expiry
 * is treated as NOT expired here (the webhook always writes one; a missing
 * value is a provisioning bug, not an expiry — the Phase-2 sweep reconciles).
 */
export function resolveDemoContext(
  metadata: DemoPublicMetadata | null | undefined,
  now: number = Date.now(),
): DemoContext | null {
  const demoRole = metadata?.demoRole ?? null;
  if (!demoRole) return null;

  const trialStartedAt = metadata?.demoTrialStartedAt ?? null;
  const trialExpiresAt = metadata?.demoTrialExpiresAt ?? null;
  const expiresMs = parseMs(trialExpiresAt);
  const expired = expiresMs !== null && now >= expiresMs;

  return {
    isDemo: true,
    demoRole,
    trialStartedAt,
    trialExpiresAt,
    expired,
  };
}

type AuthLike = () => Promise<{
  sessionClaims?: { metadata?: DemoPublicMetadata } | null;
  // Older Clerk shapes expose publicMetadata directly off the claims.
  publicMetadata?: DemoPublicMetadata | null;
}>;

/**
 * Server wrapper: read the current request's demo context via Clerk `auth()`.
 *
 * `authFn` is injected (defaults to Clerk's `auth`) so this stays unit-testable
 * without a live session. Returns `null` for non-demo users.
 */
export async function getDemoContext(
  authFn?: AuthLike,
  now: number = Date.now(),
): Promise<DemoContext | null> {
  const resolvedAuth =
    authFn ?? ((await import("@clerk/nextjs/server")).auth as unknown as AuthLike);
  const claims = await resolvedAuth();
  const metadata =
    claims?.sessionClaims?.metadata ?? claims?.publicMetadata ?? null;
  return resolveDemoContext(metadata, now);
}
