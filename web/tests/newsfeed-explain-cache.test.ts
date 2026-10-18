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

  it("keeps the shared draft after the first reader leaves (REL-327)", async () => {
    let release: (value: { text: string; provider: string; model: string }) => void = () => {};
    let modelSignal: AbortSignal | undefined;
    chat.mockImplementation((args: { signal?: AbortSignal }) => new Promise((resolve, reject) => {
      modelSignal = args.signal;
      release = resolve;
      args.signal?.addEventListener("abort", () => {
        reject(new DOMException("The operation was aborted", "AbortError"));
      });
    }));
    const { explainNewsfeedPost, resetExplainSingleFlight } = await import("@/lib/newsfeedExplainGenerate");
    resetExplainSingleFlight();
    const first = new AbortController();
    const second = new AbortController();
    const left = explainNewsfeedPost(input, { signal: first.signal, onStage: () => {} });
    await vi.waitFor(() => expect(chat).toHaveBeenCalledTimes(1));
    const stayed = explainNewsfeedPost(input, { signal: second.signal, onStage: () => {} });
    // The cache read is async. Abort only after the second reader has joined
    // the flight; joining must not open a second model call.
    await vi.waitFor(() => expect(modelSignal).toBeDefined());
    await new Promise((resolve) => setImmediate(resolve));
    expect(chat).toHaveBeenCalledTimes(1);
    first.abort();
    await expect(left).rejects.toMatchObject({ name: "AbortError" });
    release({
      text: "The auction paid plants to stay available.",
      provider: "anthropic",
      model: "claude-opus-5",
    });
    await expect(stayed).resolves.toMatchObject({
      explanation: "The auction paid plants to stay available.",
      cached: false,
    });
    expect(chat).toHaveBeenCalledTimes(1);
    expect(modelSignal?.aborted).toBe(false);
    expect(second.signal.aborted).toBe(false);
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

  it("strips a trailing disclaimer before the cache write", async () => {
    chat.mockResolvedValue({
      text: "The auction paid plants to stay available. This is not investment advice.",
      provider: "anthropic",
      model: "claude-opus-5",
    });
    const { explainNewsfeedPost, resetExplainSingleFlight } = await import("@/lib/newsfeedExplainGenerate");
    resetExplainSingleFlight();
    const out = await explainNewsfeedPost(input, {
      signal: new AbortController().signal,
      onStage: () => {},
    });
    expect(out.explanation).toBe("The auction paid plants to stay available.");
    expect(out.explanation).not.toMatch(/investment advice/i);
    const stored = await db.execute("SELECT explanation FROM newsfeed_explanations");
    expect(String(stored.rows[0].explanation)).toBe("The auction paid plants to stay available.");
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

  it("does not follow redirects away from the admitted image host", async () => {
    const fetchMock = vi.fn(async () => new Response(null, { status: 404 }));
    vi.stubGlobal("fetch", fetchMock);
    const { loadExplainImages } = await import("@/lib/newsfeedExplainImages");
    await loadExplainImages(["https://media.radon.run/newsfeed/chart.png"]);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledWith(
      "https://media.radon.run/newsfeed/chart.png",
      expect.objectContaining({ redirect: "error" }),
    );
    vi.unstubAllGlobals();
  });
});
