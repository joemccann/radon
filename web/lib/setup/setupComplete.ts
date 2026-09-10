/**
 * First-run setup completion latch (REL-192).
 *
 * A marker file under the repo root records that the wizard finished. Edge
 * middleware cannot read the filesystem, so `instrumentation.ts` and the
 * complete route mirror the marker into `RADON_SETUP_COMPLETE=1`.
 */

import { existsSync } from "node:fs";
import { promises as fs } from "node:fs";
import path from "node:path";

import { SETUP_COMPLETE_ENV } from "@/lib/setup/setupCompleteFlag";

export { SETUP_COMPLETE_ENV } from "@/lib/setup/setupCompleteFlag";
export const SETUP_MARKER_REL = ".radon/setup-complete";

export function isValidRepoRoot(root: string): boolean {
  return (
    existsSync(path.join(root, "package.json"))
    && existsSync(path.join(root, "web", "package.json"))
  );
}

/** Resolve monorepo root from the Next.js app dir (`web/`). */
export function resolveRepoRoot(cwd: string = process.cwd()): string | null {
  const candidate = path.resolve(cwd, "..");
  return isValidRepoRoot(candidate) ? candidate : null;
}

export function setupMarkerPath(repoRoot: string): string {
  return path.join(repoRoot, SETUP_MARKER_REL);
}

export async function markSetupComplete(repoRoot: string): Promise<string> {
  if (!isValidRepoRoot(repoRoot)) {
    throw new Error(`invalid repo root: ${repoRoot}`);
  }
  const marker = setupMarkerPath(repoRoot);
  await fs.mkdir(path.dirname(marker), { recursive: true });
  await fs.writeFile(marker, `${new Date().toISOString()}\n`, { mode: 0o600 });
  process.env[SETUP_COMPLETE_ENV] = "1";
  return marker;
}

/** Node boot: promote an on-disk marker into the env flag the Edge gate reads. */
export async function syncSetupCompleteFromMarker(
  cwd: string = process.cwd(),
): Promise<boolean> {
  const root = resolveRepoRoot(cwd);
  if (!root) return false;
  try {
    await fs.access(setupMarkerPath(root));
    process.env[SETUP_COMPLETE_ENV] = "1";
    return true;
  } catch {
    return false;
  }
}
