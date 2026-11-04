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

import { isSetupCompleteFlagSet } from "@/lib/setup/setupCompleteFlag";

export function isSetupComplete(
  setupCompleteFlag: string | undefined = process.env.RADON_SETUP_COMPLETE,
): boolean {
  return isSetupCompleteFlagSet(setupCompleteFlag);
}

/**
 * The setup lifecycle, derived in ONE place so the gates are mutually
 * exclusive and exhaustive:
 *
 *   setup          no Clerk key and the wizard has not latched
 *   misconfigured  latched without keys, OR a partial key pair (either key
 *                  alone cannot run Clerk, and must not fall through to it)
 *   configured     both keys present
 */
export type SetupPhase = "setup" | "misconfigured" | "configured";

export function resolveSetupPhase(
  publishableKey: string | undefined = process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY,
  secretKey: string | undefined = process.env.CLERK_SECRET_KEY,
  setupCompleteFlag: string | undefined = process.env.RADON_SETUP_COMPLETE,
): SetupPhase {
  const hasPk = Boolean((publishableKey || "").trim());
  const hasSk = Boolean((secretKey || "").trim());
  if (hasPk && hasSk) return "configured";
  if (hasPk || hasSk) return "misconfigured";
  return isSetupComplete(setupCompleteFlag) ? "misconfigured" : "setup";
}

export function isSetupMode(
  publishableKey: string | undefined = process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY,
  secretKey: string | undefined = process.env.CLERK_SECRET_KEY,
  setupCompleteFlag: string | undefined = process.env.RADON_SETUP_COMPLETE,
): boolean {
  return resolveSetupPhase(publishableKey, secretKey, setupCompleteFlag) === "setup";
}

/** Clerk keys are not fully loaded in this process: latched without keys, or a partial pair. */
export function isAuthMisconfigured(
  publishableKey: string | undefined = process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY,
  secretKey: string | undefined = process.env.CLERK_SECRET_KEY,
  setupCompleteFlag: string | undefined = process.env.RADON_SETUP_COMPLETE,
): boolean {
  return resolveSetupPhase(publishableKey, secretKey, setupCompleteFlag) === "misconfigured";
}

export const SETUP_PAGE_PATH = "/setup";
export const SETUP_API_PREFIX = "/api/setup/";

export function isSetupPath(pathname: string): boolean {
  return pathname === SETUP_PAGE_PATH || pathname.startsWith(SETUP_API_PREFIX);
}
