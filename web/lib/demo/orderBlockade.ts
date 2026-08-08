// Order blockade for demo users (demo.radon.run, Phase 3).
//
// A demo user's order placement is redirected to the paper-fill engine and can
// never reach the real IB order path. This is the UX-layer guarantee; the
// backend guarantee is the demo VM running RADON_API_TEST_MODE=1 (no IB at
// all). Both are present; neither is load-bearing alone (plan §Guardrails).
//
// Pure decision function (Clerk auth injected) so the route stays a thin switch
// and the policy is unit-tested.

import { getDemoContext, type DemoContext } from "./demoRole";

export type DemoOrderDecision =
  | { action: "allow" } // not a demo user — real IB path
  | { action: "block-expired"; trialExpiresAt: string | null } // demo, trial over
  | { action: "paper" } // demo, active — route to paper fills
  | { action: "auth-unavailable" }; // cohort UNKNOWN — refuse, never guess

/**
 * Does an unresolvable auth lookup fail CLOSED?
 *
 * Mirrors `isLocalDevAuthBypassEnabled` in web/middleware.ts: production is the
 * only environment where Clerk is actually enforced, so it is the only one where
 * `auth()` throwing means "a real request whose cohort we could not read".
 * Everywhere else an unresolvable lookup means "no Clerk in this environment" —
 * unit tests (no request context) and `next dev` on localhost (the middleware
 * bypasses Clerk outright) — which is the non-demo case by construction.
 */
export function isFailClosedEnvironment(
  nodeEnv: string | undefined = process.env.NODE_ENV,
): boolean {
  return nodeEnv === "production";
}

/** Shared copy for the fail-closed branch (no em dashes in user-facing text). */
export const AUTH_UNAVAILABLE_MESSAGE = "Sign-in state could not be verified.";

type AuthLike = Parameters<typeof getDemoContext>[0];

export async function resolveDemoOrderDecision(opts?: {
  authFn?: AuthLike;
  now?: number;
  nodeEnv?: string;
}): Promise<DemoOrderDecision> {
  let ctx: DemoContext | null;
  try {
    ctx = await getDemoContext(opts?.authFn, opts?.now);
  } catch {
    // Clerk could not answer, so the caller's cohort is UNKNOWN. Assuming
    // "not a demo user" here (the pre-T-018 behavior) put a demo user on the
    // REAL IB path on the first Clerk hiccup. Refuse instead: a real operator
    // would otherwise silently lose an order they believe reached IB, and a
    // demo user would silently place one. Outside production Clerk is not
    // enforced at all, so a throw there is the non-demo case (see
    // isFailClosedEnvironment).
    return isFailClosedEnvironment(opts?.nodeEnv)
      ? { action: "auth-unavailable" }
      : { action: "allow" };
  }
  if (!ctx) return { action: "allow" };
  if (ctx.expired) {
    return { action: "block-expired", trialExpiresAt: ctx.trialExpiresAt };
  }
  return { action: "paper" };
}

/** Order-management actions that have NO paper equivalent (see below). */
const LIVE_ONLY_ORDER_ACTIONS = {
  modify: { verb: "modify", participle: "modified" },
  cancel: { verb: "cancel", participle: "cancelled" },
} as const;

export type LiveOnlyOrderAction = keyof typeof LIVE_ONLY_ORDER_ACTIONS;

export type DemoOrderRefusal = {
  status: number;
  code: "DEMO_ORDER_BLOCKED" | "DEMO_TRIAL_EXPIRED" | "AUTH_UNAVAILABLE";
  message: string;
};

/**
 * HTTP refusal for a decision on an order-management action with no paper
 * equivalent: FastAPI exposes only `/paper/place`, and a paper fill settles
 * immediately, so a demo user has no working order to modify or cancel.
 * Placement (which DOES have a paper path) is handled in the place route.
 *
 * Returns `null` only for `allow` — the caller may then reach the live IB path.
 */
export function demoOrderRefusal(
  decision: DemoOrderDecision,
  action: LiveOnlyOrderAction,
): DemoOrderRefusal | null {
  const { verb, participle } = LIVE_ONLY_ORDER_ACTIONS[action];
  switch (decision.action) {
    case "allow":
      return null;
    case "paper":
      return {
        status: 403,
        code: "DEMO_ORDER_BLOCKED",
        message: `Demo accounts cannot ${verb} live orders. Paper fills settle immediately.`,
      };
    case "block-expired":
      return {
        status: 403,
        code: "DEMO_TRIAL_EXPIRED",
        message: `Demo trial expired. Order not ${participle}.`,
      };
    case "auth-unavailable":
      return {
        status: 503,
        code: "AUTH_UNAVAILABLE",
        message: `${AUTH_UNAVAILABLE_MESSAGE} Order not ${participle}. Retry in a moment.`,
      };
  }
}
