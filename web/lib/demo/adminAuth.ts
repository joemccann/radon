// Next.js-side operator gate for the demo-users admin API (Phase 6).
//
// The /admin/demo-users routes manage trials + write Clerk metadata, so they
// must be operator-only — not merely "any signed-in user" (which on the demo
// deployment would include trial users themselves). Mirrors the FastAPI
// ALLOWED_USER_IDS gate (scripts/api/auth.py) on the Next.js side.
//
// Default-deny: if ALLOWED_USER_IDS is unset, NOBODY is an admin.

import { parseAllowedUserIds } from "./provisionTrial";

type AuthLike = () => Promise<{ userId?: string | null }>;

export type AdminGateOptions = {
  authFn?: AuthLike;
  allowedRaw?: string;
};

async function defaultAuth(): Promise<{ userId?: string | null }> {
  const { auth } = await import("@clerk/nextjs/server");
  return auth();
}

/**
 * Resolve the operator identity for an admin request.
 * @returns the userId when allowlisted, or `null` to reject (403).
 */
export async function requireDemoAdmin(
  opts: AdminGateOptions = {},
): Promise<string | null> {
  const { userId } = await (opts.authFn ?? defaultAuth)();
  if (!userId) return null;
  const allowed = parseAllowedUserIds(opts.allowedRaw ?? process.env.ALLOWED_USER_IDS);
  return allowed.has(userId) ? userId : null;
}
