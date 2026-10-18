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
 * Pure resolver over a metadata object. Returns `null` when the user is NOT a
 * demo user (no `demoRole`) — default-deny: absence of the role means "not a
 * trial", so non-demo callers get `null` and skip every demo gate.
 *
 * `pending` is true while provisioning is unfinished: the webhook stamps
 * `demoRole: "pending"` before computing the expiry, so a brand-new user has
 * the role but no parseable expiry yet. `expired` means access is denied and
 * is true for a pending user too (fail closed), or once `now` reaches the
 * expiry. Callers that route users check `pending` first so a half-provisioned
 * signup lands on /demo-pending instead of /trial-expired.
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
  // A tagged demo identity is active only with an affirmative, parseable,
  // future expiry. Pending/malformed metadata must never become access.
  const pending = demoRole === "pending" || expiresMs === null;
  const expired = pending || now >= (expiresMs as number);

  return {
    isDemo: true,
    demoRole,
    trialStartedAt,
    trialExpiresAt,
    expired,
    pending,
  };
}
