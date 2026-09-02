/**
 * First-run setup mode — active only when NO Clerk keys are configured.
 *
 * A fresh clone has no auth to protect anything with, and nothing worth
 * protecting yet: setup mode locks the whole app down to /setup (plus its
 * API), where the wizard collects bootstrap + vendor credentials guarded by
 * a one-shot console token. The moment Clerk keys exist (the restart after
 * the wizard writes them), this returns false everywhere and the setup
 * surface hard-refuses with 404.
 *
 * Edge-safe: pure env reads, no node:* (the middleware imports this).
 */

export function isSetupMode(
  publishableKey: string | undefined = process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY,
  secretKey: string | undefined = process.env.CLERK_SECRET_KEY,
): boolean {
  return !(publishableKey || "").trim() && !(secretKey || "").trim();
}

export const SETUP_PAGE_PATH = "/setup";
export const SETUP_API_PREFIX = "/api/setup/";

export function isSetupPath(pathname: string): boolean {
  return pathname === SETUP_PAGE_PATH || pathname.startsWith(SETUP_API_PREFIX);
}
