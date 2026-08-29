/**
 * LLM model catalog — the single source of truth for which models this
 * deployment can actually serve.
 *
 * The chat picker is derived, never hardcoded: a provider appears only when its
 * API key is present in THIS process's environment. A keyless provider is
 * ABSENT, not a disabled placeholder — a greyed row still tells the operator
 * the model exists here when it does not. One available provider means a
 * one-entry picker; that is honest, not a bug.
 *
 * Resolution is three tiers, in order:
 *   1. Turso `llm_model_catalog` — refreshed daily by scripts/refresh_model_catalog.py
 *   2. data/llm_models.json     — the same job's atomic disk fallback
 *   3. BUILTIN_FRONTIER         — compiled in, so the picker still works cold
 * Each tier is asked only for the providers still MISSING a model, so a
 * provider absent from Turso (a key added between daily catalog runs) is
 * filled from disk, else from the builtin, rather than vanishing from the
 * picker. A tier that yields nothing at all (missing table, schema drift,
 * unreadable file, empty result) simply contributes nothing. `source` names
 * the tier that answered FIRST.
 *
 * Server-only: reads Turso and the data/ tree. Never import from a client
 * component — fetch GET /api/models instead.
 *
 * A key added to /etc/radon/env is visible here only after the Next.js process
 * restarts, because the picker reads the unit's own process.env.
 */

import { readFile } from "fs/promises";
import { join } from "path";

import { dbExecute } from "@/lib/dbExecute";
import { DEFAULT_MODELS } from "./frontier";
import { resolveProvider, resolveXaiApiKey } from "./provider";

export type LlmModelProvider = "anthropic" | "openai" | "xai";

export type LlmModelOption = {
  /** Wire model id passed straight to the provider, e.g. "claude-opus-5". */
  id: string;
  provider: LlmModelProvider;
  /** Terminal-cased short label for the picker, e.g. "CLAUDE OPUS 5". */
  label: string;
  /** ISO date the catalog row was refreshed. Drives honest freshness copy. */
  refreshedAt: string;
};

/** Which of the three tiers produced `models`. */
export type LlmCatalogSource = "turso" | "disk" | "builtin";

export type LlmModelCatalog = {
  models: LlmModelOption[];
  /** Always an id present in `models`, or "" when no provider is configured. */
  defaultId: string;
  source: LlmCatalogSource;
};

export const LLM_MODEL_PROVIDERS: readonly LlmModelProvider[] = ["anthropic", "openai", "xai"];

/**
 * Compiled-in last resort. Every id is a bare, undated, currently-GA model
 * verified against the provider's live model list on the date below.
 *
 * OpenAI is deliberately gpt-5.5, not a gpt-5.6 tier: GPT-5.6 access is still
 * per-org, and a fallback the key cannot call turns a degraded path into a 404.
 */
export const BUILTIN_FRONTIER: Record<LlmModelProvider, { id: string; label: string }> = {
  anthropic: { id: DEFAULT_MODELS.anthropic, label: labelForModelId(DEFAULT_MODELS.anthropic) },
  openai: { id: DEFAULT_MODELS.openai, label: labelForModelId(DEFAULT_MODELS.openai) },
  xai: { id: DEFAULT_MODELS.xai, label: labelForModelId(DEFAULT_MODELS.xai) },
};

/**
 * The date the BUILTIN_FRONTIER ids were verified against live model lists —
 * not a runtime clock. The UI derives its freshness copy from whichever
 * `refreshedAt` it is handed, so a stale builtin must read as stale.
 */
export const BUILTIN_REFRESHED_AT = "2026-08-29";

const CATALOG_PATH = join(process.cwd(), "..", "data", "llm_models.json");

/**
 * Anthropic's key aliases. provider.ts owns the canonical list but does not
 * export its Anthropic resolver, and this module must not reach a key value —
 * only its presence. Keep in lockstep with ANTHROPIC_ENV_KEYS there.
 */
const ANTHROPIC_ENV_KEYS = ["ANTHROPIC_API_KEY", "CLAUDE_CODE_API_KEY", "CLAUDE_API_KEY"];

function hasEnvValue(key: string): boolean {
  return Boolean(process.env[key]?.trim());
}

/**
 * Which providers this deployment can serve. Returns presence only — no key
 * material crosses this boundary, so nothing downstream can leak one.
 */
export function availableProviders(): LlmModelProvider[] {
  const available: LlmModelProvider[] = [];
  if (ANTHROPIC_ENV_KEYS.some(hasEnvValue)) available.push("anthropic");
  if (hasEnvValue("OPENAI_API_KEY")) available.push("openai");
  if (resolveXaiApiKey()) available.push("xai");
  return available;
}

/** "claude-opus-5" -> "CLAUDE OPUS 5". Dots survive (xAI ids use them). */
export function labelForModelId(id: string): string {
  return id.replace(/-/g, " ").toUpperCase();
}

function isProvider(value: unknown): value is LlmModelProvider {
  return typeof value === "string" && (LLM_MODEL_PROVIDERS as readonly string[]).includes(value);
}

function cleanString(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

/**
 * Normalize one catalog row. Serves both writers: SQL rows arrive snake_cased
 * from Turso, the JSON fallback carries the same field names.
 */
function toModelOption(raw: Record<string, unknown>, fallbackRefreshedAt: string): LlmModelOption | null {
  const provider = raw.provider;
  if (!isProvider(provider)) return null;

  const id = cleanString(raw.model_id) || cleanString(raw.id);
  if (!id) return null;

  const refreshedAt = cleanString(raw.refreshed_at) || fallbackRefreshedAt;
  if (!refreshedAt) return null;

  return {
    id,
    provider,
    label: cleanString(raw.display_name) || cleanString(raw.label) || labelForModelId(id),
    refreshedAt,
  };
}

function keepAvailable(models: LlmModelOption[], available: LlmModelProvider[]): LlmModelOption[] {
  const seen = new Set<string>();
  return models.filter((model) => {
    if (!available.includes(model.provider)) return false;
    if (seen.has(model.id)) return false;
    seen.add(model.id);
    return true;
  });
}

async function readFromTurso(): Promise<LlmModelOption[]> {
  const result = await dbExecute(
    {
      sql: `SELECT provider, model_id, display_name, refreshed_at
          FROM llm_model_catalog ORDER BY provider ASC, model_id ASC`,
      args: [],
    },
    { label: "llm_model_catalog" },
  );
  return result.rows
    .map((row) => toModelOption(row as unknown as Record<string, unknown>, ""))
    .filter((model): model is LlmModelOption => model !== null);
}

async function readFromDisk(): Promise<LlmModelOption[]> {
  const parsed = JSON.parse(await readFile(CATALOG_PATH, "utf-8")) as {
    refreshed_at?: unknown;
    models?: unknown;
  };
  const rows = Array.isArray(parsed?.models) ? parsed.models : [];
  const fallbackRefreshedAt = cleanString(parsed?.refreshed_at);
  return rows
    .map((row) => toModelOption((row ?? {}) as Record<string, unknown>, fallbackRefreshedAt))
    .filter((model): model is LlmModelOption => model !== null);
}

function readFromBuiltin(available: LlmModelProvider[]): LlmModelOption[] {
  return available.map((provider) => ({
    id: BUILTIN_FRONTIER[provider].id,
    provider,
    label: BUILTIN_FRONTIER[provider].label,
    refreshedAt: BUILTIN_REFRESHED_AT,
  }));
}

/**
 * The default the picker preselects. Delegates precedence to provider.ts's own
 * resolveProvider() so an unspecified /api/assistant request and the picker's
 * default can never disagree. A provider it names that has no catalogued model
 * here (e.g. gemini, which the picker does not carry) falls to the first
 * available entry.
 */
function pickDefaultId(models: LlmModelOption[]): string {
  if (models.length === 0) return "";
  const preferred = resolveProvider();
  return models.find((model) => model.provider === preferred)?.id ?? models[0].id;
}

async function tierModels(read: () => Promise<LlmModelOption[]>, available: LlmModelProvider[]) {
  try {
    return keepAvailable(await read(), available);
  } catch {
    // A missing table, schema drift, an unreadable or malformed file: every
    // failure is a silent fall-through to the next tier, never a thrown route.
    return [];
  }
}

/**
 * The three-tier read. Never throws; the worst case is the builtin tier.
 *
 * Tiers are consulted in order for the providers still MISSING a model, not
 * winner-take-all: the catalog job runs daily, so a key added to the unit env
 * this morning has no Turso row until tomorrow, and dropping that provider
 * would hide a model the deployment can serve right now. Every available
 * provider therefore ends up with an entry, worst case the compiled-in
 * frontier dated honestly as a builtin. `source` names the tier that answered
 * FIRST, which is the tier the operator's catalog job owns.
 */
export async function resolveModelCatalog(): Promise<LlmModelCatalog> {
  const available = availableProviders();
  if (available.length === 0) {
    return { models: [], defaultId: "", source: "builtin" };
  }

  const models: LlmModelOption[] = [];
  let source: LlmCatalogSource | null = null;
  const missing = () => available.filter((provider) => !models.some((m) => m.provider === provider));

  for (const [tier, read] of [
    ["turso", readFromTurso],
    ["disk", readFromDisk],
  ] as const) {
    const wanted = missing();
    if (wanted.length === 0) break;
    const rows = await tierModels(read, wanted);
    if (rows.length === 0) continue;
    models.push(...rows);
    source ??= tier;
  }

  const uncovered = missing();
  if (uncovered.length > 0) {
    models.push(...readFromBuiltin(uncovered));
    source ??= "builtin";
  }

  return { models, defaultId: pickDefaultId(models), source: source ?? "builtin" };
}

/**
 * Server-side gate for a client-supplied model id. Returns the catalogued
 * option (so the caller gets its provider too) or null. Anything not in this
 * deployment's catalog is rejected, so a client can never bill an arbitrary
 * model string.
 */
export async function validateModelId(id: unknown): Promise<LlmModelOption | null> {
  const wanted = cleanString(id);
  if (!wanted) return null;
  const { models } = await resolveModelCatalog();
  return models.find((model) => model.id === wanted) ?? null;
}
