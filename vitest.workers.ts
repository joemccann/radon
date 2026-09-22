/**
 * Worker budget for the root vitest config.
 *
 * `maxWorkers: "100%"` spawned 14 jsdom workers on the operator's laptop and,
 * beside a `pytest -n auto` run, pushed the machine 8 GB into swap
 * (2026-09-07). CI keeps every core (4 vCPUs); a developer machine keeps half
 * so pytest can share it. `VITEST_MAX_WORKERS` is the explicit override.
 */
export function resolveMaxWorkers(env: NodeJS.ProcessEnv): number | string {
  const explicit = Number(env.VITEST_MAX_WORKERS);
  if (env.VITEST_MAX_WORKERS && Number.isInteger(explicit) && explicit > 0) return explicit;
  return env.CI ? "100%" : "50%";
}
