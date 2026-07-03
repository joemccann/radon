// Demo trial context resolution (demo.radon.run).
//
// The PURE resolver + types live in demoContext.ts (client-safe — the welcome
// modal imports them there); this module re-exports them for the existing
// server/edge consumers and adds `getDemoContext`, a thin server wrapper that
// pulls `sessionClaims.metadata` (or falls back to `publicMetadata`) via
// Clerk's `auth()`. Do NOT import this module from a Client Component: the
// dynamic `@clerk/nextjs/server` import trips Turbopack's 'server-only' guard.
//
// Edge-safe: NO `node:*` imports. The Phase-2 middleware expiry gate imports
// from this module and runs in the Edge runtime (see
// feedback_middleware_edge_runtime).

import { resolveDemoContext, type DemoPublicMetadata, type DemoContext } from "./demoContext";

export {
  resolveDemoContext,
  type DemoContext,
  type DemoPublicMetadata,
  type DemoRole,
} from "./demoContext";

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
