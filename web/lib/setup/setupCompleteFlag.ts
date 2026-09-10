/**
 * Edge-safe setup completion flag (REL-192).
 *
 * Middleware reads only `RADON_SETUP_COMPLETE=1`; marker-file sync lives in
 * `setupComplete.ts` (Node-only).
 */

export const SETUP_COMPLETE_ENV = "RADON_SETUP_COMPLETE";

export function isSetupCompleteFlagSet(
  flag: string | undefined = process.env[SETUP_COMPLETE_ENV],
): boolean {
  return flag === "1";
}
