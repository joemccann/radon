import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createClient, type Client } from "@libsql/client";
import { readFileSync } from "node:fs";
import path from "node:path";

const MIGRATION = readFileSync(
  path.join(__dirname, "../../scripts/db/migrations/0096_newsfeed_explanations.sql"),
  "utf8",
);

const { chat, resolveProvider } = vi.hoisted(() => ({
  chat: vi.fn(),
  resolveProvider: vi.fn(() => "anthropic"),
}));
vi.mock("@/lib/llm/provider", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/llm/provider")>();
  return { ...actual, chat, resolveProvider, alternateProvider: () => "xai" };
});

let db: Client;

async function exec(sql: string) {
  for (const stmt of sql.split(";").map((s) => s.replace(/--.*$/gm, "").trim()).filter(Boolean)) {
    await db.execute(stmt);
  }
}

beforeEach(async () => {
  vi.resetModules();
  chat.mockReset();
  db = createClient({ url: ":memory:" });
  await db.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)");
  await exec(MIGRATION);
  (await import("../lib/db")).__setDbForTests(db);
});

afterEach(async () => {
  vi.restoreAllMocks();
  (await import("../lib/db")).__resetDbForTests();
  db.close();
});

const input = {
  postId: "pjm-2028-capacity",
  title: "PJM capacity auction cleared at the cap",
  content: "The 2028/2029 auction cleared at $325/MW-day and came up 6.8 GW short of the requirement.",
  imageUrls: [] as string[],
};

describe("newsfeed explain cache and generate", () => {
  it("returns a Turso hit without calling the model", async () => {
    const { writeExplainCache } = await import("@/lib/newsfeedExplainCache");
    const { explainContentHash } = await import("@/lib/newsfeedExplain");
    const { explainNewsfeedPost } = await import("@/lib/newsfeedExplainGenerate");
    const hash = explainContentHash(input);
    await writeExplainCache({
      postId: input.postId,
      contentHash: hash,
      explanation: "Cached plain English.",
      model: "claude-opus-5",
      provider: "anthropic",
      hasImage: false,
    });
    const stages: string[] = [];
    const out = await explainNewsfeedPost(input, {
      signal: new AbortController().signal,
      onStage: (stage) => stages.push(stage),
    });
    expect(out).toMatchObject({ explanation: "Cached plain English.", cached: true, thin: false });
    expect(chat).not.toHaveBeenCalled();
    expect(stages).toContain("loading");
  });

  it("single-flights concurrent misses into one model call", async () => {
    chat.mockImplementation(() => new Promise((resolve) => {
      setTimeout(() => resolve({
        text: "The auction paid plants to stay available.",
        provider: "anthropic",
        model: "claude-opus-5",
      }), 20);
    }));
    const { explainNewsfeedPost, resetExplainSingleFlight } = await import("@/lib/newsfeedExplainGenerate");
    resetExplainSingleFlight();
    const opts = { signal: new AbortController().signal, onStage: () => {} };
    const [a, b] = await Promise.all([
      explainNewsfeedPost(input, opts),
      explainNewsfeedPost(input, opts),
    ]);
    expect(a.explanation).toBe(b.explanation);
    expect(chat).toHaveBeenCalledTimes(1);
    const stored = await db.execute("SELECT COUNT(*) AS n FROM newsfeed_explanations");
    expect(Number(stored.rows[0].n)).toBe(1);
  });

  it("skips the model for thin posts and never invents a market call", async () => {
    const { explainNewsfeedPost } = await import("@/lib/newsfeedExplainGenerate");
    const { EXPLAIN_THIN_MESSAGE } = await import("@/lib/newsfeedExplain");
    const out = await explainNewsfeedPost(
      { postId: "thin", title: "Hi", content: "Ok.", imageUrls: [] },
      { signal: new AbortController().signal, onStage: () => {} },
    );
    expect(out.thin).toBe(true);
    expect(out.explanation).toBe(EXPLAIN_THIN_MESSAGE);
    expect(chat).not.toHaveBeenCalled();
  });

  it("refuses loopback image fetches before a socket opens", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const { loadExplainImages } = await import("@/lib/newsfeedExplainImages");
    const images = await loadExplainImages(["http://localhost/health", "https://attacker.example/x.png"]);
    expect(images).toEqual([]);
    expect(fetchMock).not.toHaveBeenCalled();
    vi.unstubAllGlobals();
  });
});
