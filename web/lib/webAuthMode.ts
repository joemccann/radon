const TRUTHY = new Set(["1", "true", "yes", "on"]);
const FALSY = new Set(["0", "false", "no", "off"]);

export function isWebAuthBypassEnabled(env: NodeJS.ProcessEnv = process.env): boolean {
  const explicit = env.RADON_BYPASS_WEB_AUTH?.trim().toLowerCase();
  if (!explicit || FALSY.has(explicit)) return false;
  if (!TRUTHY.has(explicit)) return false;

  return env.NODE_ENV !== "production";
}
