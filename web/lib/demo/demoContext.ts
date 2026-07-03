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
