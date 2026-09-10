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
 * Quoting is per-CONSUMER, verified empirically against both parsers
 * (REL-191 / R-523; round-trip tests: web/tests/setup-env-files-durability
 * .test.ts against the real @next/env, and scripts/tests/
 * test_rel191_root_env_dotenv_roundtrip.py against the real python-dotenv):
 *
 * - python-dotenv (root .env): single quotes are fully literal but CANNOT
 *   contain a single quote (the shell `'\''` idiom makes the whole statement
 *   unparseable); double quotes unescape `\\` and `\"` and interpolate
 *   `${VAR}` (bare `$` is literal).
 * - @next/env (web/.env): runs dotenv-expand, which expands `$VAR`/`${VAR}`
 *   even inside SINGLE quotes; `\$` yields a literal `$`; inside double
 *   quotes `\n` becomes a real newline and a literal `"` cannot be
 *   represented.
 *
 * Values neither dialect can encode are REFUSED loudly — a truncated
 * credential (the `$`-blanking incident class,
 * feedback_env_file_shell_expansion.md) is strictly worse than an error.
 */

import { promises as fs } from "node:fs";
import crypto from "node:crypto";
import path from "node:path";

// Keys the web process needs (web/.env). Everything the wizard collects goes
// into the root .env for the Python stack; this subset is duplicated for Next.
// Also the only names the completion route accepts while the FastAPI registry
// is unreachable (the offline path exists to get Clerk + Turso into place).
export const WEB_ENV_KEYS = new Set([
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

export type EnvDialect = "python" | "next";

class EnvEncodingError extends Error {}

function refuse(key: string, reason: string): never {
  throw new EnvEncodingError(
    `${key} cannot be safely written to a .env file (${reason}); ` +
      "set it via the credentials panel or by editing the file by hand",
  );
}

function quotePython(key: string, value: string): string {
  if (/[\n\r]/.test(value)) refuse(key, "value contains a newline");
  if (!value.includes("'")) return `'${value}'`;
  // Double quotes: python-dotenv unescapes \\ and \" but interpolates ${VAR}.
  if (value.includes("${")) {
    refuse(key, "value combines a single quote with ${ interpolation syntax");
  }
  return `"${value.replace(/\\/g, "\\\\").replace(/"/g, '\\"')}"`;
}

function quoteNext(key: string, value: string): string {
  if (/[\n\r]/.test(value)) refuse(key, "value contains a newline");
  if (!value.includes("$")) {
    // dotenv-expand leaves dollar-free values alone in either quote style.
    if (!value.includes("'")) return `'${value}'`;
    if (value.includes('"') || value.includes("\\")) {
      refuse(key, "value mixes quotes/backslashes @next/env cannot represent");
    }
    return `"${value}"`;
  }
  // `$` must be escaped as \$ inside double quotes; a literal `"` or `\`
  // alongside `$` has no faithful encoding under @next/env's parser.
  if (value.includes('"') || value.includes("\\")) {
    refuse(key, "value mixes $ with quotes/backslashes @next/env cannot represent");
  }
  return `"${value.replace(/\$/g, "\\$")}"`;
}

function quote(key: string, value: string, dialect: EnvDialect): string {
  return dialect === "next" ? quoteNext(key, value) : quotePython(key, value);
}

export function upsertEnvContent(
  existing: string,
  entries: Record<string, string>,
  dialect: EnvDialect = "python",
): string {
  const lines = existing.length > 0 ? existing.split("\n") : [];
  const remaining = new Map(Object.entries(entries));
  const seen = new Set<string>();
  // Scan a value for an unterminated quote: a `KEY="...` line opens a
  // multiline statement in both declared parsers, and every line until the
  // closing quote is VALUE CONTENT, not an assignment (REL-218 / R-593 —
  // rewriting a `UW_TOKEN=` line inside a quoted block broke the whole
  // statement through real python-dotenv).
  const openQuoteOf = (value: string, from: string | null): string | null => {
    let open = from;
    for (let i = 0; i < value.length; i += 1) {
      const ch = value[i];
      if (open) {
        if (ch === "\\" && open === '"') { i += 1; continue; }
        if (ch === open) open = null;
      } else if (i === 0 && (ch === '"' || ch === "'")) {
        open = ch;
      } else if (!from && i > 0) {
        break; // only a value that STARTS quoted can continue across lines
      }
    }
    return open;
  };
  // EVERY occurrence of a managed key is rewritten: both parsers apply
  // last-assignment-wins, so leaving a later duplicate untouched silently
  // discards the wizard's value (R-546).
  let openQuote: string | null = null;
  let dropContinuation = false;
  const next: string[] = [];
  for (const line of lines) {
    if (openQuote) {
      openQuote = openQuoteOf(line, openQuote);
      // Inside a multiline value: either value content of an unmanaged key
      // (preserve verbatim) or the tail of a managed key's OLD value we just
      // replaced (drop it, or the fragment corrupts the file).
      if (!dropContinuation) next.push(line);
      if (!openQuote) dropContinuation = false;
      continue;
    }
    const match = /^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=/.exec(line);
    if (!match) {
      next.push(line);
      continue;
    }
    const key = match[1];
    const value = line.slice(line.indexOf("=") + 1).trim();
    openQuote = openQuoteOf(value, null);
    if (!remaining.has(key)) {
      next.push(line);
      continue;
    }
    seen.add(key);
    next.push(`${key}=${quote(key, remaining.get(key)!, dialect)}`);
    if (openQuote) dropContinuation = true;
  }
  for (const key of seen) remaining.delete(key);
  if (remaining.size > 0) {
    if (next.length > 0 && next[next.length - 1] !== "") next.push("");
    next.push("# Written by the Radon first-run setup wizard");
    for (const [key, value] of remaining) {
      next.push(`${key}=${quote(key, value, dialect)}`);
    }
    next.push("");
  }
  return next.join("\n");
}

async function upsertEnvFile(
  filePath: string,
  entries: Record<string, string>,
  dialect: EnvDialect,
): Promise<void> {
  let existing = "";
  try {
    existing = await fs.readFile(filePath, "utf8");
  } catch {}
  const content = upsertEnvContent(existing, entries, dialect);
  // Temp + rename in the same directory (the repo's atomic_io.py convention):
  // a crash mid-write must never leave the operator's existing secrets
  // truncated by O_TRUNC on the live file (R-524).
  const tmpPath = path.join(
    path.dirname(filePath),
    `.${path.basename(filePath)}.${crypto.randomBytes(6).toString("hex")}.tmp`,
  );
  try {
    await fs.writeFile(tmpPath, content, { encoding: "utf8", mode: 0o600 });
    await fs.rename(tmpPath, filePath);
  } catch (error) {
    await fs.rm(tmpPath, { force: true }).catch(() => {});
    throw error;
  }
  await fs.chmod(filePath, 0o600).catch(() => {});
}

// One write at a time across the two files: both tabs can hold the setup
// token, and an interleaved read-modify-write silently drops one tab's keys.
let writeChain: Promise<unknown> = Promise.resolve();

/**
 * Write collected values into the repo root .env (everything, python-dotenv
 * dialect) and web/.env (the web subset, @next/env dialect). Returns the
 * file paths written.
 */
/** REL-216 (R-591): pre-check each value against the dialect(s) its file
 * needs. A refused value must be dropped and REPORTED, never thrown after
 * credentials were stored and before the setup latch — that wedged
 * onboarding permanently. */
export function partitionEnvEncodable(values: Record<string, string>): {
  encodable: Record<string, string>;
  refused: Array<{ key: string; message: string }>;
} {
  const encodable: Record<string, string> = {};
  const refused: Array<{ key: string; message: string }> = [];
  for (const [key, value] of Object.entries(values)) {
    try {
      quote(key, value, "python");
      if (WEB_ENV_KEYS.has(key)) quote(key, value, "next");
      encodable[key] = value;
    } catch (error) {
      if (error instanceof EnvEncodingError) {
        refused.push({ key, message: error.message });
      } else {
        throw error;
      }
    }
  }
  return { encodable, refused };
}


export async function writeSetupEnvFiles(
  values: Record<string, string>,
  repoRoot: string = path.resolve(process.cwd(), ".."),
): Promise<string[]> {
  const run = async (): Promise<string[]> => {
    const written: string[] = [];
    const rootEnv = path.join(repoRoot, ".env");

    // R-623: each file is written atomically but the PAIR was not. Root .env
    // was renamed into place and a throw on the web write propagated out of
    // the route with root .env already committed and markSetupComplete /
    // consumeSetupToken never reached — a stack with CLERK_SECRET_KEY present
    // and the publishable key absent, which is exactly the isAuthMisconfigured
    // shape the middleware turns into a terminal page with no way back to
    // /setup. Snapshot the root file so the pair can be rolled back.
    let rootBefore: string | null = null;
    try {
      rootBefore = await fs.readFile(rootEnv, "utf8");
    } catch {
      rootBefore = null;
    }
    const rollbackRoot = async (): Promise<void> => {
      try {
        if (rootBefore === null) {
          await fs.rm(rootEnv, { force: true });
        } else {
          await fs.writeFile(rootEnv, rootBefore, { encoding: "utf8", mode: 0o600 });
        }
      } catch {
        // Best effort: the original error is the one worth propagating.
      }
    };

    await upsertEnvFile(rootEnv, values, "python");
    written.push(rootEnv);

    const webValues: Record<string, string> = {};
    for (const [key, value] of Object.entries(values)) {
      if (WEB_ENV_KEYS.has(key)) webValues[key] = value;
    }
    if (Object.keys(webValues).length > 0) {
      const webEnv = path.join(repoRoot, "web", ".env");
      try {
        await upsertEnvFile(webEnv, webValues, "next");
      } catch (error) {
        await rollbackRoot();
        throw error;
      }
      written.push(webEnv);
    }
    return written;
  };
  const result = writeChain.then(run, run);
  writeChain = result.catch(() => {});
  return result;
}
