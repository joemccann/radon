/**
 * @vitest-environment node
 *
 * LLM model catalog — web/lib/llm/catalog.ts + GET /api/models.
 *
 * The picker this feeds must be HONEST: it lists exactly the providers whose
 * API key is present in THIS deployment's environment. A provider with no key
 * is absent from the list, never a disabled placeholder, because a disabled
 * row still tells the operator the model exists here when it does not.
 *
 * Resolution is a three-tier read — Turso `llm_model_catalog` (written daily by
 * scripts/refresh_model_catalog.py) -> data/llm_models.json -> the compiled-in
 * builtin constant — and `source` names the tier that actually answered.
 *
 * Contract: `validateModelId` is the server-side gate agent I3 imports so a
 * client can never bill an arbitrary model string.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { createClient, type Client } from "@libsql/client";

let db: Client;
const mockGetDb = vi.fn(() => db);
vi.mock("@/lib/db", () => ({ resetDb: () => {}, getDb: mockGetDb }));

const mockReadFile = vi.fn();
vi.mock("fs/promises", async (importOriginal) => {
  const actual = await importOriginal<typeof import("fs/promises")>();
  return { ...actual, readFile: (...args: unknown[]) => mockReadFile(...args) };
});

// Distinctive sentinels. If any of these strings reaches a response body or a
// model option, the catalog is leaking key material.
const ANTHROPIC_KEY = "sk-ant-SENTINEL-anthropic-0001";
const XAI_KEY = "xai-SENTINEL-0002";
const OPENAI_KEY = "sk-SENTINEL-openai-0003";

const PROVIDER_ENV_KEYS = [
  "ANTHROPIC_API_KEY",
  "CLAUDE_CODE_API_KEY",
  "CLAUDE_API_KEY",
  "XAI_API_KEY",
  "GROK_API_KEY",
  "OPENAI_API_KEY",
  "GEMINI_API_KEY",
  "LLM_PROVIDER",
];

async function seedSchema(client: Client): Promise<void> {
  // Mirrors scripts/db/migrations/*_llm_model_catalog.sql (agent I4). A column
  // drift between the two is a DB read failure, which degrades to disk.
  await client.execute(`CREATE TABLE llm_model_catalog (
    provider TEXT NOT NULL,
    model_id TEXT NOT NULL,
    display_name TEXT,
    refreshed_at TEXT NOT NULL,
    PRIMARY KEY (provider, model_id))`);
}

// Window-relative: never a hardcoded refresh date.
function daysAgo(n: number): string {
  const d = new Date();
  d.setUTCDate(d.getUTCDate() - n);
  return d.toISOString().slice(0, 10);
}

const DB_REFRESHED_AT = daysAgo(0);
const DISK_REFRESHED_AT = daysAgo(3);

async function insertRow(provider: string, modelId: string, label: string): Promise<void> {
  await db.execute({
    sql: `INSERT INTO llm_model_catalog (provider, model_id, display_name, refreshed_at)
          VALUES (?, ?, ?, ?)`,
    args: [provider, modelId, label, DB_REFRESHED_AT],
  });
}

function diskPayload(models: Array<Record<string, unknown>>): string {
  return JSON.stringify({ refreshed_at: DISK_REFRESHED_AT, models });
}

beforeEach(async () => {
  vi.clearAllMocks();
  vi.resetModules();
  for (const key of PROVIDER_ENV_KEYS) vi.stubEnv(key, "");
  db = createClient({ url: ":memory:" });
  await seedSchema(db);
  mockGetDb.mockImplementation(() => db);
  // Default: no disk fallback file.
  mockReadFile.mockRejectedValue(Object.assign(new Error("ENOENT"), { code: "ENOENT" }));
});

afterEach(() => {
  vi.unstubAllEnvs();
  db?.close();
});

async function loadCatalog() {
  return import("@/lib/llm/catalog");
}

describe("provider key presence — the honesty contract", () => {
  it("lists only providers whose API key is present in this environment", async () => {
    vi.stubEnv("ANTHROPIC_API_KEY", ANTHROPIC_KEY);
    vi.stubEnv("XAI_API_KEY", XAI_KEY);

    const { resolveModelCatalog } = await loadCatalog();
    const catalog = await resolveModelCatalog();

    expect(catalog.models.map((m) => m.provider).sort()).toEqual(["anthropic", "xai"]);
  });

  it("omits a keyless provider entirely rather than disabling it", async () => {
    vi.stubEnv("ANTHROPIC_API_KEY", ANTHROPIC_KEY);

    const { resolveModelCatalog } = await loadCatalog();
    const catalog = await resolveModelCatalog();

    expect(catalog.models).toHaveLength(1);
    expect(catalog.models[0].provider).toBe("anthropic");
    expect(catalog.models.some((m) => m.provider === "openai")).toBe(false);
    // No disabled/available placeholder shape anywhere in the payload.
    expect(JSON.stringify(catalog)).not.toMatch(/disabled|unavailable|"available"/);
  });

  it("honors the xAI key aliases provider.ts already resolves", async () => {
    vi.stubEnv("GROK_API_KEY", XAI_KEY);

    const { resolveModelCatalog } = await loadCatalog();
    const catalog = await resolveModelCatalog();

    expect(catalog.models.map((m) => m.provider)).toEqual(["xai"]);
  });

  it("returns an empty catalog when no provider key is configured", async () => {
    const { resolveModelCatalog } = await loadCatalog();
    const catalog = await resolveModelCatalog();

    expect(catalog.models).toEqual([]);
    expect(catalog.defaultId).toBe("");
  });
});

describe("three-tier resolution", () => {
  it("serves Turso rows over disk and reports source=turso", async () => {
    vi.stubEnv("ANTHROPIC_API_KEY", ANTHROPIC_KEY);
    await insertRow("anthropic", "claude-opus-9", "CLAUDE OPUS 9");
    mockReadFile.mockResolvedValue(
      diskPayload([{ provider: "anthropic", model_id: "claude-from-disk", display_name: "DISK" }]),
    );

    const { resolveModelCatalog } = await loadCatalog();
    const catalog = await resolveModelCatalog();

    expect(catalog.source).toBe("turso");
    expect(catalog.models.map((m) => m.id)).toEqual(["claude-opus-9"]);
    expect(catalog.models[0].refreshedAt).toBe(DB_REFRESHED_AT);
  });

  it("serves disk over builtin when Turso has no rows and reports source=disk", async () => {
    vi.stubEnv("ANTHROPIC_API_KEY", ANTHROPIC_KEY);
    mockReadFile.mockResolvedValue(
      diskPayload([{ provider: "anthropic", model_id: "claude-from-disk", display_name: "DISK MODEL" }]),
    );

    const { resolveModelCatalog } = await loadCatalog();
    const catalog = await resolveModelCatalog();

    expect(catalog.source).toBe("disk");
    expect(catalog.models.map((m) => m.id)).toEqual(["claude-from-disk"]);
    expect(catalog.models[0].refreshedAt).toBe(DISK_REFRESHED_AT);
  });

  it("falls back to the compiled-in builtin when both Turso and disk are empty", async () => {
    vi.stubEnv("ANTHROPIC_API_KEY", ANTHROPIC_KEY);

    const { resolveModelCatalog, BUILTIN_FRONTIER } = await loadCatalog();
    const catalog = await resolveModelCatalog();

    expect(catalog.source).toBe("builtin");
    expect(catalog.models.map((m) => m.id)).toEqual([BUILTIN_FRONTIER.anthropic.id]);
  });

  it("degrades to disk without throwing when the Turso read fails", async () => {
    vi.stubEnv("ANTHROPIC_API_KEY", ANTHROPIC_KEY);
    mockGetDb.mockImplementation(() => {
      throw new Error("Turso unavailable");
    });
    mockReadFile.mockResolvedValue(
      diskPayload([{ provider: "anthropic", model_id: "claude-from-disk", display_name: "DISK MODEL" }]),
    );

    const { resolveModelCatalog } = await loadCatalog();
    const catalog = await resolveModelCatalog();

    expect(catalog.source).toBe("disk");
    expect(catalog.models.map((m) => m.id)).toEqual(["claude-from-disk"]);
  });

  it("degrades all the way to builtin when Turso throws and disk is unreadable", async () => {
    vi.stubEnv("ANTHROPIC_API_KEY", ANTHROPIC_KEY);
    mockGetDb.mockImplementation(() => {
      throw new Error("Turso unavailable");
    });
    mockReadFile.mockResolvedValue("{ this is not json");

    const { resolveModelCatalog } = await loadCatalog();
    const catalog = await resolveModelCatalog();

    expect(catalog.source).toBe("builtin");
    expect(catalog.models).toHaveLength(1);
  });

  it("backfills an available provider the answering tier has no row for", async () => {
    // A key added to /etc/radon/env is live the moment Next.js restarts, but
    // the catalog job only fires daily. The provider must not be invisible for
    // the rest of the day - "lights up when the key lands" is the whole design.
    vi.stubEnv("ANTHROPIC_API_KEY", ANTHROPIC_KEY);
    vi.stubEnv("XAI_API_KEY", XAI_KEY);
    await insertRow("anthropic", "claude-opus-9", "CLAUDE OPUS 9");

    const { resolveModelCatalog, BUILTIN_FRONTIER, BUILTIN_REFRESHED_AT } = await loadCatalog();
    const catalog = await resolveModelCatalog();

    expect(catalog.models.map((m) => m.provider).sort()).toEqual(["anthropic", "xai"]);
    const xai = catalog.models.find((m) => m.provider === "xai");
    expect(xai?.id).toBe(BUILTIN_FRONTIER.xai.id);
    // The backfilled row must date itself honestly, not borrow the DB stamp.
    expect(xai?.refreshedAt).toBe(BUILTIN_REFRESHED_AT);
    expect(catalog.source).toBe("turso");
  });

  it("fills a Turso gap from disk before reaching for the builtin", async () => {
    vi.stubEnv("ANTHROPIC_API_KEY", ANTHROPIC_KEY);
    vi.stubEnv("XAI_API_KEY", XAI_KEY);
    await insertRow("anthropic", "claude-opus-9", "CLAUDE OPUS 9");
    mockReadFile.mockResolvedValue(
      diskPayload([{ provider: "xai", model_id: "grok-from-disk", display_name: "DISK GROK" }]),
    );

    const { resolveModelCatalog } = await loadCatalog();
    const catalog = await resolveModelCatalog();

    const xai = catalog.models.find((m) => m.provider === "xai");
    expect(xai?.id).toBe("grok-from-disk");
    expect(xai?.refreshedAt).toBe(DISK_REFRESHED_AT);
  });

  it("drops catalogued rows for providers this deployment has no key for", async () => {
    vi.stubEnv("ANTHROPIC_API_KEY", ANTHROPIC_KEY);
    await insertRow("anthropic", "claude-opus-9", "CLAUDE OPUS 9");
    await insertRow("openai", "gpt-9", "GPT 9");
    await insertRow("xai", "grok-9", "GROK 9");

    const { resolveModelCatalog } = await loadCatalog();
    const catalog = await resolveModelCatalog();

    expect(catalog.models.map((m) => m.id)).toEqual(["claude-opus-9"]);
  });
});

describe("defaultId", () => {
  it("is always an id present in models[]", async () => {
    vi.stubEnv("ANTHROPIC_API_KEY", ANTHROPIC_KEY);
    vi.stubEnv("XAI_API_KEY", XAI_KEY);
    vi.stubEnv("OPENAI_API_KEY", OPENAI_KEY);

    const { resolveModelCatalog } = await loadCatalog();
    const catalog = await resolveModelCatalog();

    expect(catalog.models.map((m) => m.id)).toContain(catalog.defaultId);
  });

  it("matches provider.ts resolveProvider() precedence — xAI key wins over Anthropic", async () => {
    vi.stubEnv("ANTHROPIC_API_KEY", ANTHROPIC_KEY);
    vi.stubEnv("XAI_API_KEY", XAI_KEY);

    const { resolveModelCatalog } = await loadCatalog();
    const catalog = await resolveModelCatalog();

    const chosen = catalog.models.find((m) => m.id === catalog.defaultId);
    expect(chosen?.provider).toBe("xai");
  });

  it("still names the resolveProvider() provider when only the OTHER provider is catalogued", async () => {
    // The disagreement this pins: an /api/assistant POST with no model goes to
    // xAI (resolveProvider auto-prefers a present XAI_API_KEY), so a picker
    // whose default fell through to the Anthropic row would advertise one
    // model and the server would run another.
    vi.stubEnv("ANTHROPIC_API_KEY", ANTHROPIC_KEY);
    vi.stubEnv("XAI_API_KEY", XAI_KEY);
    await insertRow("anthropic", "claude-opus-9", "CLAUDE OPUS 9");

    const { resolveModelCatalog } = await loadCatalog();
    const catalog = await resolveModelCatalog();

    const chosen = catalog.models.find((m) => m.id === catalog.defaultId);
    expect(chosen?.provider).toBe("xai");
  });

  it("follows an explicit LLM_PROVIDER override, as an unspecified request would", async () => {
    vi.stubEnv("ANTHROPIC_API_KEY", ANTHROPIC_KEY);
    vi.stubEnv("XAI_API_KEY", XAI_KEY);
    vi.stubEnv("LLM_PROVIDER", "anthropic");

    const { resolveModelCatalog } = await loadCatalog();
    const catalog = await resolveModelCatalog();

    const chosen = catalog.models.find((m) => m.id === catalog.defaultId);
    expect(chosen?.provider).toBe("anthropic");
  });
});

describe("BUILTIN_FRONTIER", () => {
  it("is the same set of ids provider.ts defaults each provider to", async () => {
    // Two halves of one feature. A catalog that offers grok-4.6 while
    // provider.ts still defaults xAI to the withdrawn grok-4 runs turns on a
    // model the picker never showed - and grok-4 reportedly bills as grok-4.3
    // instead of erroring, so the drift is silent.
    const { BUILTIN_FRONTIER, LLM_MODEL_PROVIDERS } = await loadCatalog();
    const { DEFAULT_MODELS } = await import("@/lib/llm/provider");

    for (const provider of LLM_MODEL_PROVIDERS) {
      expect(BUILTIN_FRONTIER[provider].id).toBe(DEFAULT_MODELS[provider]);
    }
  });
});

describe("validateModelId", () => {
  it("accepts a catalogued id and returns its provider", async () => {
    vi.stubEnv("ANTHROPIC_API_KEY", ANTHROPIC_KEY);
    await insertRow("anthropic", "claude-opus-9", "CLAUDE OPUS 9");

    const { validateModelId } = await loadCatalog();
    const model = await validateModelId("claude-opus-9");

    expect(model).not.toBeNull();
    expect(model?.provider).toBe("anthropic");
  });

  it("rejects an arbitrary client-supplied string", async () => {
    vi.stubEnv("ANTHROPIC_API_KEY", ANTHROPIC_KEY);
    await insertRow("anthropic", "claude-opus-9", "CLAUDE OPUS 9");

    const { validateModelId } = await loadCatalog();

    expect(await validateModelId("gpt-4o-i-made-this-up")).toBeNull();
    expect(await validateModelId("")).toBeNull();
    expect(await validateModelId(null)).toBeNull();
  });

  it("rejects a catalogued id belonging to a provider with no key here", async () => {
    vi.stubEnv("ANTHROPIC_API_KEY", ANTHROPIC_KEY);
    await insertRow("openai", "gpt-9", "GPT 9");

    const { validateModelId } = await loadCatalog();

    expect(await validateModelId("gpt-9")).toBeNull();
  });
});

describe("GET /api/models", () => {
  it("returns models, defaultId and source, and never key material", async () => {
    vi.stubEnv("ANTHROPIC_API_KEY", ANTHROPIC_KEY);
    vi.stubEnv("XAI_API_KEY", XAI_KEY);
    vi.stubEnv("OPENAI_API_KEY", OPENAI_KEY);
    await insertRow("anthropic", "claude-opus-9", "CLAUDE OPUS 9");
    await insertRow("xai", "grok-9", "GROK 9");
    await insertRow("openai", "gpt-9", "GPT 9");

    const { GET } = await import("@/app/api/models/route");
    const response = await GET();
    const body = await response.text();

    expect(response.status).toBe(200);
    for (const secret of [ANTHROPIC_KEY, XAI_KEY, OPENAI_KEY]) {
      expect(body).not.toContain(secret);
    }

    const payload = JSON.parse(body) as {
      models: Array<{ id: string; provider: string; label: string; refreshedAt: string }>;
      defaultId: string;
      source: string;
    };
    expect(payload.source).toBe("turso");
    expect(payload.models.map((m) => m.id).sort()).toEqual(["claude-opus-9", "gpt-9", "grok-9"]);
    expect(payload.models.map((m) => m.id)).toContain(payload.defaultId);
    for (const model of payload.models) {
      expect(model.label).toBe(model.label.toUpperCase());
      expect(model.refreshedAt).toBe(DB_REFRESHED_AT);
    }
  });

  it("stays a 200 with an empty list when no provider key is configured", async () => {
    const { GET } = await import("@/app/api/models/route");
    const response = await GET();
    const payload = (await response.json()) as { models: unknown[]; defaultId: string };

    expect(response.status).toBe(200);
    expect(payload.models).toEqual([]);
    expect(payload.defaultId).toBe("");
  });

  it("opts out of the static route cache", async () => {
    const route = await import("@/app/api/models/route");
    expect(route.dynamic).toBe("force-dynamic");
  });
});
