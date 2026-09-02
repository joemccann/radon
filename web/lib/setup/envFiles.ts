/**
 * .env materialization for the first-run wizard.
 *
 * The secret store holds the runtime copy, but two consumers need real env
 * files before they can even boot: Next.js (NEXT_PUBLIC_* is inlined at
 * build/dev start, Clerk middleware reads its keys from env) and the Python
 * stack (python-dotenv loads the repo root .env). The wizard therefore also
 * writes the collected values into those files, merge-preserving anything
 * already there.
 *
 * Values are single-quoted: `$` inside a bare value shell-expands under
 * `set -a` loaders and has silently blanked credentials before (see
 * feedback_env_file_shell_expansion.md).
 */

import { promises as fs } from "node:fs";
import path from "node:path";

// Keys the web process needs (web/.env). Everything the wizard collects goes
// into the root .env for the Python stack; this subset is duplicated for Next.
const WEB_ENV_KEYS = new Set([
  "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY",
  "CLERK_SECRET_KEY",
  "TURSO_DB_URL",
  "TURSO_AUTH_TOKEN",
  "UW_TOKEN",
  "ANTHROPIC_API_KEY",
  "CEREBRAS_API_KEY",
  "XAI_API_KEY",
  "EXA_API_KEY",
]);

function quote(value: string): string {
  return `'${value.replace(/'/g, `'\\''`)}'`;
}

export function upsertEnvContent(
  existing: string,
  entries: Record<string, string>,
): string {
  const lines = existing.length > 0 ? existing.split("\n") : [];
  const remaining = new Map(Object.entries(entries));
  const next = lines.map((line) => {
    const match = /^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=/.exec(line);
    if (!match) return line;
    const key = match[1];
    if (!remaining.has(key)) return line;
    const value = remaining.get(key)!;
    remaining.delete(key);
    return `${key}=${quote(value)}`;
  });
  if (remaining.size > 0) {
    if (next.length > 0 && next[next.length - 1] !== "") next.push("");
    next.push("# Written by the Radon first-run setup wizard");
    for (const [key, value] of remaining) {
      next.push(`${key}=${quote(value)}`);
    }
    next.push("");
  }
  return next.join("\n");
}

async function upsertEnvFile(
  filePath: string,
  entries: Record<string, string>,
): Promise<void> {
  let existing = "";
  try {
    existing = await fs.readFile(filePath, "utf8");
  } catch {}
  const content = upsertEnvContent(existing, entries);
  await fs.writeFile(filePath, content, { encoding: "utf8", mode: 0o600 });
  await fs.chmod(filePath, 0o600).catch(() => {});
}

/**
 * Write collected values into the repo root .env (everything) and web/.env
 * (the web subset). Returns the file paths written.
 */
export async function writeSetupEnvFiles(
  values: Record<string, string>,
  repoRoot: string = path.resolve(process.cwd(), ".."),
): Promise<string[]> {
  const written: string[] = [];
  const rootEnv = path.join(repoRoot, ".env");
  await upsertEnvFile(rootEnv, values);
  written.push(rootEnv);

  const webValues: Record<string, string> = {};
  for (const [key, value] of Object.entries(values)) {
    if (WEB_ENV_KEYS.has(key)) webValues[key] = value;
  }
  if (Object.keys(webValues).length > 0) {
    const webEnv = path.join(repoRoot, "web", ".env");
    await upsertEnvFile(webEnv, webValues);
    written.push(webEnv);
  }
  return written;
}
