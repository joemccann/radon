/**
 * First-run setup mode — active only on an unconfigured clone that has NOT
 * finished the wizard yet.
 *
 * After the wizard completes, a repo-root marker is written and mirrored into
 * `RADON_SETUP_COMPLETE=1` (Edge-safe). A configured host that loses its
 * Clerk env on a running process must NOT re-open /setup — that is the
 * separate auth-misconfigured gate.
 *
 * Edge-safe: pure env reads, no node:* (the middleware imports this).
 */

import { isSetupCompleteFlagSet } from "@/lib/setup/setupComplete";

function clerkKeysAbsent(
  publishableKey: string | undefined = process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY,
  secretKey: string | undefined = process.env.CLERK_SECRET_KEY,
): boolean {
  return !(publishableKey || "").trim() && !(secretKey || "").trim();
}

export function isSetupComplete(
  setupCompleteFlag: string | undefined = process.env.RADON_SETUP_COMPLETE,
): boolean {
  return isSetupCompleteFlagSet(setupCompleteFlag);
}

export function isSetupMode(
  publishableKey: string | undefined = process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY,
  secretKey: string | undefined = process.env.CLERK_SECRET_KEY,
  setupCompleteFlag: string | undefined = process.env.RADON_SETUP_COMPLETE,
): boolean {
  if (isSetupComplete(setupCompleteFlag)) return false;
  return clerkKeysAbsent(publishableKey, secretKey);
}

/** Wizard finished but Clerk keys are not loaded in this process yet. */
export function isAuthMisconfigured(
  publishableKey: string | undefined = process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY,
  secretKey: string | undefined = process.env.CLERK_SECRET_KEY,
  setupCompleteFlag: string | undefined = process.env.RADON_SETUP_COMPLETE,
): boolean {
  return isSetupComplete(setupCompleteFlag) && clerkKeysAbsent(publishableKey, secretKey);
}

export const SETUP_PAGE_PATH = "/setup";
export const SETUP_API_PREFIX = "/api/setup/";

export function isSetupPath(pathname: string): boolean {
  return pathname === SETUP_PAGE_PATH || pathname.startsWith(SETUP_API_PREFIX);
}
