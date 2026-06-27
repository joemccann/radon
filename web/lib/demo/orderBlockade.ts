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
  | { action: "paper" }; // demo, active — route to paper fills

type AuthLike = Parameters<typeof getDemoContext>[0];

export async function resolveDemoOrderDecision(opts?: {
  authFn?: AuthLike;
  now?: number;
}): Promise<DemoOrderDecision> {
  const ctx: DemoContext | null = await getDemoContext(opts?.authFn, opts?.now);
  if (!ctx) return { action: "allow" };
  if (ctx.expired) {
    return { action: "block-expired", trialExpiresAt: ctx.trialExpiresAt };
  }
  return { action: "paper" };
}
