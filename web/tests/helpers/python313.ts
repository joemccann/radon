/**
 * Gate for the handful of vitest tests that spawn a REAL `python3.13`.
 *
 * The vitest gate runs on a Bun-only CI job (`ci.yml` `web-tests`) with no
 * Python at all. Until T-276 that was handled by three `--exclude` flags in
 * the workflow, which took whole FILES out of every shard — and those files
 * are mixed: only 9 of their 27 tests spawn Python. The other 18 (PI route
 * dispatch, `/api/prices` deprecation, `resolveProjectRoot`, and the
 * 2026-05-22 bare-`python3.13` outage regression in `resolvePythonBin`) need
 * no subprocess whatsoever, yet ran NOWHERE: excluded in CI, and red on any
 * developer machine without `python3.13` on PATH.
 *
 * So the split is per TEST, not per file. The subprocess tests skip by name
 * with the reason in the title, which is visible in the run output, instead of
 * being silently dropped in a workflow file nobody reads.
 *
 * The probe deliberately checks `numpy` too: `scripts/kelly.py` imports it, so
 * a bare interpreter with no repo deps is not a machine these tests can run
 * on. It does NOT probe the heavier `clients.uw_client` tree that the
 * `--help` screens need — if THAT is missing on a machine that has
 * python3.13 + numpy, the test failing is the correct signal, not a skip.
 */
import { spawnSync } from "node:child_process";

let cached: boolean | null = null;

export function hasPython313(): boolean {
  if (cached !== null) return cached;
  const probe = spawnSync("python3.13", ["-c", "import numpy"], { stdio: "ignore" });
  cached = probe.status === 0;
  return cached;
}

const REASON =
  "SKIPPED: spawns a real python3.13 (+ repo deps); the Bun-only CI vitest shard has no Python — T-276";

/** Title that names the skip reason when, and only when, the block will skip. */
export function python313Label(name: string): string {
  return hasPython313() ? name : `${name} — ${REASON}`;
}
