/**
 * Subscription credentials for the LLM layer.
 *
 * Operator mandate 2026-09-18: every AI call meters against the operator's
 * subscriptions (Claude Max, SuperGrok, ChatGPT via Codex), never a prepaid
 * console key for Anthropic, xAI, OpenAI or Google. The grants live in the
 * CLI credential files that `radon-subscription-tokens` keeps live on the host
 * and `radon-app-runtime` binds read-only into every app container:
 *
 *   anthropic  ~/.claude/.credentials.json   claudeAiOauth.accessToken
 *   xai        ~/.grok/auth.json             <issuer>::<client>.key
 *   openai     ~/.codex/auth.json            tokens.access_token (+ account_id)
 *
 * Prepaid keys are honoured only under RADON_LADDER_ALLOW_PREPAID=1, the same
 * escape hatch scripts/clients/model_ladder.py uses, and never as a fallback.
 *
 * Node-only: reads files lazily through require("node:fs") so the module stays
 * importable where node:fs is absent (no HOME, no override: no read).
 */

const FILE_TTL_MS = 30_000;

export type SubscriptionGrant = {
  token: string;
  /** ChatGPT account the Codex grant belongs to; sent as `chatgpt-account-id`. */
  accountId?: string;
};

function envValue(key: string): string | undefined {
  const value = process.env[key]?.trim();
  return value ? value : undefined;
}

/** `RADON_LADDER_ALLOW_PREPAID=1|true|yes|on` re-enables prepaid console keys. */
export function allowPrepaid(): boolean {
  const raw = (process.env.RADON_LADDER_ALLOW_PREPAID ?? "").trim().toLowerCase();
  return raw === "1" || raw === "true" || raw === "yes" || raw === "on";
}

const fileCache = new Map<string, { readAt: number; doc: unknown }>();

function readJsonFile(filePath: string): unknown {
  const now = Date.now();
  const cached = fileCache.get(filePath);
  if (cached && now - cached.readAt < FILE_TTL_MS) return cached.doc;
  let doc: unknown;
  try {
    const fs = require("node:fs") as typeof import("node:fs");
    doc = JSON.parse(fs.readFileSync(filePath, "utf8"));
  } catch {
    doc = undefined; // missing, unreadable or malformed: no grant
  }
  fileCache.set(filePath, { readAt: now, doc });
  return doc;
}

/** Test seam: forget cached file reads. */
export function resetSubscriptionAuthCache(): void {
  fileCache.clear();
}

function homeDir(): string | undefined {
  const home = envValue("HOME");
  return home ? home.replace(/\/$/, "") : undefined;
}

function isObject(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function nonEmpty(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
}

function expired(value: unknown): boolean {
  if (typeof value === "number") return Number.isFinite(value) && value <= Date.now();
  if (typeof value === "string") {
    const at = Date.parse(value);
    return Number.isFinite(at) && at <= Date.now();
  }
  return false;
}

// --- anthropic --------------------------------------------------------------

/** Claude Code OAuth: CLAUDE_CODE_OAUTH_TOKEN, else the credentials file. */
export function resolveAnthropicSubscription(): SubscriptionGrant | undefined {
  const fromEnv = envValue("CLAUDE_CODE_OAUTH_TOKEN");
  if (fromEnv) return { token: fromEnv };
  const dir = envValue("CLAUDE_CONFIG_DIR") ?? (homeDir() ? `${homeDir()}/.claude` : undefined);
  if (!dir) return undefined;
  const doc = readJsonFile(`${dir.replace(/\/$/, "")}/.credentials.json`);
  if (!isObject(doc) || !isObject(doc.claudeAiOauth)) return undefined;
  const oauth = doc.claudeAiOauth;
  const token = nonEmpty(oauth.accessToken) ?? nonEmpty(oauth.access_token);
  if (!token || expired(oauth.expiresAt)) return undefined;
  return { token };
}

// --- xai ----------------------------------------------------------------------

/** SuperGrok OIDC grant: XAI_OAUTH_TOKEN / GROK_OAUTH_TOKEN, else ~/.grok/auth.json. */
export function resolveXaiSubscription(): SubscriptionGrant | undefined {
  const fromEnv = envValue("XAI_OAUTH_TOKEN") ?? envValue("GROK_OAUTH_TOKEN");
  if (fromEnv) return { token: fromEnv };
  const filePath = envValue("GROK_AUTH_FILE") ?? (homeDir() ? `${homeDir()}/.grok/auth.json` : undefined);
  if (!filePath) return undefined;
  const doc = readJsonFile(filePath);
  if (!isObject(doc)) return undefined;
  for (const entry of Object.values(doc)) {
    if (!isObject(entry)) continue;
    const token = nonEmpty(entry.key);
    if (!token || expired(entry.expires_at)) continue;
    return { token };
  }
  return undefined;
}

// --- openai (ChatGPT via Codex) ---------------------------------------------

/**
 * ChatGPT subscription grant the codex CLI writes. api.openai.com rejects it
 * ("no credits remaining": that host meters the prepaid wallet); the grant is
 * accepted by chatgpt.com/backend-api/codex/responses with the account id.
 */
export function resolveCodexSubscription(): SubscriptionGrant | undefined {
  const fromEnv = envValue("CODEX_OAUTH_TOKEN");
  if (fromEnv) return { token: fromEnv, accountId: envValue("CODEX_ACCOUNT_ID") };
  const dir = envValue("CODEX_HOME") ?? (homeDir() ? `${homeDir()}/.codex` : undefined);
  if (!dir) return undefined;
  const doc = readJsonFile(`${dir.replace(/\/$/, "")}/auth.json`);
  if (!isObject(doc)) return undefined;
  const mode = nonEmpty(doc.auth_mode)?.toLowerCase();
  if (mode === "apikey" || mode === "api_key") return undefined; // a prepaid login, not a subscription
  const tokens = isObject(doc.tokens) ? doc.tokens : undefined;
  const token = nonEmpty(tokens?.access_token) ?? nonEmpty(tokens?.accessToken);
  if (!token) return undefined;
  return { token, accountId: nonEmpty(tokens?.account_id) };
}
