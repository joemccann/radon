import { mkdtemp, mkdir, readdir, readFile, rm, writeFile, stat } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import vm from "node:vm";
import { describe, expect, it, afterEach } from "vitest";
import { JSDOM } from "jsdom";

let tempRoot: string | null = null;

async function createTempRoot() {
  tempRoot = await mkdtemp(path.join(tmpdir(), "radon-newsfeed-test-"));
  return tempRoot;
}

afterEach(async () => {
  if (tempRoot) {
    await rm(tempRoot, { recursive: true, force: true });
    tempRoot = null;
  }
});

describe("seedPostsFileIfMissing", () => {
  it("creates posts.json stub when missing", async () => {
    const root = await createTempRoot();
    const dataDir = path.join(root, "data");
    const postsFile = path.join(dataDir, "posts.json");

    const { seedPostsFileIfMissing } = await import("../../scripts/newsfeed/paths.js");

    const created = await seedPostsFileIfMissing({ dataDir, postsFile });

    expect(created).toBe(true);
    expect(await stat(postsFile)).toBeTruthy();
    expect(JSON.parse(await readFile(postsFile, "utf8"))).toEqual([]);
  });

  it("does not overwrite existing posts.json contents", async () => {
    const root = await createTempRoot();
    const dataDir = path.join(root, "data");
    const postsFile = path.join(dataDir, "posts.json");

    await mkdir(dataDir, { recursive: true });
    await writeFile(postsFile, JSON.stringify([{ id: "abc" }], null, 2));

    const { seedPostsFileIfMissing } = await import("../../scripts/newsfeed/paths.js");

    const created = await seedPostsFileIfMissing({ dataDir, postsFile });

    expect(created).toBe(false);
    expect(JSON.parse(await readFile(postsFile, "utf8"))).toEqual([{ id: "abc" }]);
  });
});

describe("mergePosts", () => {
  it("inserts new posts with createdAt and updatedAt", async () => {
    const { mergePosts } = await import("../../scripts/newsfeed/store.js");

    const fakeNow = () => new Date("2025-04-01T12:00:00Z");
    const { merged, changed } = mergePosts(
      [],
      [
        {
          id: "p1",
          title: "Tesla up",
          content: "+5%",
          timestamp: "2025-04-01T11:50:00Z",
          images: ["https://x/y.jpg"],
        },
      ],
      { now: fakeNow },
    );

    expect(changed).toBe(true);
    expect(merged).toHaveLength(1);
    expect(merged[0]).toMatchObject({
      id: "p1",
      title: "Tesla up",
      images: [],
      rawImages: ["https://x/y.jpg"],
      createdAt: "2025-04-01T12:00:00.000Z",
      updatedAt: "2025-04-01T12:00:00.000Z",
    });
  });

  it("only updates existing posts on real diff", async () => {
    const { mergePosts } = await import("../../scripts/newsfeed/store.js");

    const existing = [
      {
        id: "p1",
        title: "Tesla up",
        content: "+5%",
        timestamp: "2025-04-01T11:50:00.000Z",
        timestampMs: new Date("2025-04-01T11:50:00.000Z").getTime(),
        rawImages: ["https://x/y.jpg"],
        images: ["/media/p1.jpg"],
        createdAt: "2025-03-30T00:00:00.000Z",
        updatedAt: "2025-03-30T00:00:00.000Z",
      },
    ];

    const noChange = mergePosts(
      existing,
      [
        {
          id: "p1",
          title: "Tesla up",
          content: "+5%",
          timestamp: "2025-04-01T11:50:00Z",
          images: ["https://x/y.jpg"],
        },
      ],
      { now: () => new Date("2025-05-01T00:00:00Z") },
    );
    expect(noChange.changed).toBe(false);
    expect(noChange.merged[0].updatedAt).toBe("2025-03-30T00:00:00.000Z");
    expect(noChange.merged[0].createdAt).toBe("2025-03-30T00:00:00.000Z");

    const withChange = mergePosts(
      existing,
      [
        {
          id: "p1",
          title: "Tesla rallies",
          content: "+5%",
          timestamp: "2025-04-01T11:50:00Z",
          images: ["https://x/y.jpg"],
        },
      ],
      { now: () => new Date("2025-05-01T00:00:00Z") },
    );
    expect(withChange.changed).toBe(true);
    expect(withChange.merged[0].title).toBe("Tesla rallies");
    expect(withChange.merged[0].createdAt).toBe("2025-03-30T00:00:00.000Z");
    expect(withChange.merged[0].updatedAt).toBe("2025-05-01T00:00:00.000Z");
  });

  it("sorts merged posts by timestamp descending", async () => {
    const { mergePosts } = await import("../../scripts/newsfeed/store.js");

    const { merged } = mergePosts(
      [],
      [
        { id: "older", title: "older", content: "", timestamp: "2025-04-01T10:00:00Z", images: [] },
        { id: "newer", title: "newer", content: "", timestamp: "2025-04-01T12:00:00Z", images: [] },
        { id: "middle", title: "middle", content: "", timestamp: "2025-04-01T11:00:00Z", images: [] },
      ],
      { now: () => new Date("2025-04-01T13:00:00Z") },
    );

    expect(merged.map((p) => p.id)).toEqual(["newer", "middle", "older"]);
  });
});

describe("persistPosts rollover", () => {
  it("writes posts.json without archive when under threshold", async () => {
    const root = await createTempRoot();
    const dataDir = path.join(root, "data");
    const archiveDir = path.join(dataDir, "archive");
    const postsFile = path.join(dataDir, "posts.json");

    const { persistPosts } = await import("../../scripts/newsfeed/store.js");

    const result = await persistPosts(
      [{ id: "p1", title: "small", timestamp: "2025-04-01T00:00:00Z", timestampMs: 0 }],
      { dataDir, archiveDir, postsFile },
    );

    expect(result.archived).toBe(false);
    const written = JSON.parse(await readFile(postsFile, "utf8"));
    expect(written).toHaveLength(1);
    expect(written[0]).not.toHaveProperty("timestampMs");
    expect(await readdir(archiveDir)).toEqual([]);
  });

  it("archives and truncates to ceil(N * 0.2) when over 500 KB", async () => {
    const root = await createTempRoot();
    const dataDir = path.join(root, "data");
    const archiveDir = path.join(dataDir, "archive");
    const postsFile = path.join(dataDir, "posts.json");

    const { persistPosts } = await import("../../scripts/newsfeed/store.js");

    const padding = "x".repeat(12_000);
    const posts = Array.from({ length: 60 }, (_, i) => ({
      id: `p${i}`,
      title: `post ${i}`,
      content: padding,
      timestamp: new Date(2025, 0, 1, 0, i, 0).toISOString(),
      timestampMs: new Date(2025, 0, 1, 0, i, 0).getTime(),
      rawImages: [],
    }));

    const result = await persistPosts(posts, {
      dataDir,
      archiveDir,
      postsFile,
      now: () => new Date("2025-04-01T00:00:00Z"),
    });

    expect(result.archived).toBe(true);
    expect(result.archiveName).toMatch(/^posts-2025-04-01T00-00-00-000Z\.json$/);
    expect(result.keepCount).toBe(Math.max(1, Math.ceil(60 * 0.2)));

    const archiveFiles = await readdir(archiveDir);
    expect(archiveFiles).toHaveLength(1);
    const archived = JSON.parse(await readFile(path.join(archiveDir, archiveFiles[0]), "utf8"));
    expect(archived).toHaveLength(60);

    const live = JSON.parse(await readFile(postsFile, "utf8"));
    expect(live).toHaveLength(12);
    expect(live[0].id).toBe("p0");
  });

  it("respects custom maxBytes for fast tests", async () => {
    const root = await createTempRoot();
    const dataDir = path.join(root, "data");
    const archiveDir = path.join(dataDir, "archive");
    const postsFile = path.join(dataDir, "posts.json");

    const { persistPosts } = await import("../../scripts/newsfeed/store.js");

    const posts = Array.from({ length: 10 }, (_, i) => ({
      id: `p${i}`,
      title: `t${i}`,
      timestamp: new Date(2025, 0, 1, 0, i, 0).toISOString(),
      timestampMs: new Date(2025, 0, 1, 0, i, 0).getTime(),
    }));

    const result = await persistPosts(posts, {
      dataDir,
      archiveDir,
      postsFile,
      maxBytes: 100,
      now: () => new Date("2025-04-02T00:00:00Z"),
    });

    expect(result.archived).toBe(true);
    expect(result.keepCount).toBe(2);
    const live = JSON.parse(await readFile(postsFile, "utf8"));
    expect(live).toHaveLength(2);
  });
});

describe("buildExtractionExpression (DOM)", () => {
  it("extracts well-formed ld+json article AND falls back to DOM when ld+json is malformed", async () => {
    const { buildExtractionExpression, parsePayload } = await import(
      "../../scripts/newsfeed/extract.js"
    );

    const fixture = `
      <article class="post" id="article-12345">
        <script type="application/ld+json">
          {"@type":"Article","headline":"Tesla rallies on demand surge","datePublished":"2025-01-15T10:00:00Z","image":["https://themarketear.com/uploads/tsla.jpg"]}
        </script>
        <h2 class="title">Tesla rallies (DOM title)</h2>
        <div class="body"><div class="content">Tesla up 5% on demand</div></div>
        <time datetime="2025-01-15T10:00:00Z">Jan 15</time>
      </article>
      <article class="post" id="article-67890">
        <script type="application/ld+json">
          {this is not valid json}
        </script>
        <h2 class="title">Apple slips on supply chain</h2>
        <div class="body"><div class="content">Apple down 3% on supply concerns</div></div>
        <time datetime="2025-01-15T11:00:00Z">Jan 15</time>
        <img src="/uploads/aapl.jpg" />
      </article>
    `;

    const dom = new JSDOM(`<!DOCTYPE html><html><body>${fixture}</body></html>`);
    const ctx: Record<string, unknown> = {
      document: dom.window.document,
      URL: globalThis.URL,
    };
    vm.createContext(ctx);
    const raw = vm.runInContext(buildExtractionExpression(), ctx) as string;

    const parsed = parsePayload(raw);
    expect(parsed.ok).toBe(true);
    if (!parsed.ok) throw new Error("expected ok payload");
    expect(parsed.items).toHaveLength(2);

    const wellFormed = parsed.items.find((it: any) => it.id === "article-12345");
    expect(wellFormed).toBeDefined();
    expect(wellFormed.title).toBe("Tesla rallies on demand surge");
    expect(wellFormed.timestamp).toBe("2025-01-15T10:00:00Z");
    expect(wellFormed.images).toContain("https://themarketear.com/uploads/tsla.jpg");

    const fallback = parsed.items.find((it: any) => it.id === "article-67890");
    expect(fallback).toBeDefined();
    expect(fallback.title).toBe("Apple slips on supply chain");
    expect(fallback.timestamp).toBe("2025-01-15T11:00:00Z");
    expect(fallback.content).toContain("Apple down 3%");
    expect(fallback.images).toContain("https://themarketear.com/uploads/aapl.jpg");
  });

  it("filters out articles missing id/title/timestamp", async () => {
    const { buildExtractionExpression, parsePayload } = await import(
      "../../scripts/newsfeed/extract.js"
    );

    const fixture = `
      <article class="post" id="ok-1">
        <h2 class="title">Has all fields</h2>
        <time datetime="2025-01-01T00:00:00Z"></time>
      </article>
      <article class="post">
        <h2 class="title">Missing id</h2>
        <time datetime="2025-01-01T00:00:00Z"></time>
      </article>
    `;

    const dom = new JSDOM(`<!DOCTYPE html><html><body>${fixture}</body></html>`);
    const ctx: Record<string, unknown> = {
      document: dom.window.document,
      URL: globalThis.URL,
    };
    vm.createContext(ctx);
    const raw = vm.runInContext(buildExtractionExpression(), ctx) as string;
    const parsed = parsePayload(raw);

    expect(parsed.ok).toBe(true);
    if (!parsed.ok) throw new Error("expected ok payload");
    expect(parsed.items).toHaveLength(1);
    expect(parsed.items[0].id).toBe("ok-1");
  });
});

describe("parsePayload", () => {
  it("returns ok+items for well-formed payload", async () => {
    const { parsePayload } = await import("../../scripts/newsfeed/extract.js");
    const result = parsePayload(JSON.stringify({ ok: true, items: [{ id: "a" }] }));
    expect(result.ok).toBe(true);
    if (!result.ok) throw new Error("unreachable");
    expect(result.items).toEqual([{ id: "a" }]);
  });

  it("returns dom error for { ok: false } payload", async () => {
    const { parsePayload } = await import("../../scripts/newsfeed/extract.js");
    const result = parsePayload(JSON.stringify({ ok: false, message: "selector failed" }));
    expect(result.ok).toBe(false);
    if (result.ok) throw new Error("unreachable");
    expect(result.source).toBe("dom");
    expect(result.reason).toBe("selector failed");
  });

  it("returns parse error for malformed JSON", async () => {
    const { parsePayload } = await import("../../scripts/newsfeed/extract.js");
    const result = parsePayload("{not json");
    expect(result.ok).toBe(false);
    if (result.ok) throw new Error("unreachable");
    expect(result.source).toBe("parse");
    expect(result.reason).toMatch(/invalid JSON/);
  });

  it("returns shape error for empty / non-string / non-object payloads", async () => {
    const { parsePayload } = await import("../../scripts/newsfeed/extract.js");

    expect(parsePayload("").ok).toBe(false);
    expect((parsePayload("") as { source: string }).source).toBe("shape");

    const arr = parsePayload(JSON.stringify([1, 2, 3]));
    expect(arr.ok).toBe(false);
    if (arr.ok) throw new Error("unreachable");
    expect(arr.source).toBe("shape");

    const noOk = parsePayload(JSON.stringify({ items: [] }));
    expect(noOk.ok).toBe(false);
    if (noOk.ok) throw new Error("unreachable");
    expect(noOk.source).toBe("shape");
  });
});
