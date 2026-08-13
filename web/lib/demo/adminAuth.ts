// Next.js-side operator gate for the demo-users admin API (Phase 6).
//
// The /api/admin/demo-users routes manage trials + write Clerk metadata, so
// they must be demo-admin-only. The operator control panel at /admin uses
// ALLOWED_USER_IDS via requireRouteAccess({ operatorOnly: true }). Do not
// reuse this helper for that page: DEMO_ADMIN_USER_IDS is unset on
// app.radon.run and would 404 the operator.
//
// Default-deny: if DEMO_ADMIN_USER_IDS is unset, NOBODY is a demo admin.

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
  const allowed = parseAllowedUserIds(opts.allowedRaw ?? process.env.DEMO_ADMIN_USER_IDS);
  return allowed.has(userId) ? userId : null;
}
