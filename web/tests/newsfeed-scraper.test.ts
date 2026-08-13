import { mkdtemp, mkdir, readdir, readFile, rm, writeFile, stat } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import vm from "node:vm";
import { describe, expect, it, afterEach, vi } from "vitest";
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

  it("propagates existing-file read errors instead of seeding empty history", async () => {
    const root = await createTempRoot();
    const postsFile = path.join(root, "data", "posts.json");
    await mkdir(postsFile, { recursive: true });
    const { seedPostsFileIfMissing } = await import("../../scripts/newsfeed/paths.js");
    await expect(seedPostsFileIfMissing({ dataDir: path.dirname(postsFile), postsFile })).rejects.toThrow();
    expect((await stat(postsFile)).isDirectory()).toBe(true);
  });
});

describe("browser lifecycle", () => {
  it("closes a launched browser when context initialization fails", async () => {
    const root = await createTempRoot();
    const storageStatePath = path.join(root, "storage.json");
    await writeFile(storageStatePath, "{}");
    const close = vi.fn(async () => {});
    const launcher = {
      launch: vi.fn(async () => ({
        newContext: vi.fn(async () => { throw new Error("corrupt storage"); }),
        close,
      })),
    };
    const { createBrowser } = await import("../../scripts/newsfeed/browser.js");
    await expect(createBrowser({ storageStatePath, launcher })).rejects.toThrow("corrupt storage");
    expect(close).toHaveBeenCalledOnce();
    expect((await readdir(root)).some((name) => name.startsWith("storage.json.corrupt-"))).toBe(true);
  });

  it("persists browser credentials atomically with mode 0600", async () => {
    const root = await createTempRoot();
    const storageStatePath = path.join(root, "storage.json");
    const context = {
      newPage: vi.fn(async () => ({})),
      cookies: vi.fn(async () => []),
      storageState: vi.fn(async () => ({ cookies: [], origins: [] })),
      close: vi.fn(async () => {}),
    };
    const browser = {
      newContext: vi.fn(async () => context),
      close: vi.fn(async () => {}),
    };
    const { createBrowser } = await import("../../scripts/newsfeed/browser.js");
    const handle = await createBrowser({ storageStatePath, launcher: { launch: async () => browser } });
    await handle.persistStorageState();
    expect(JSON.parse(await readFile(storageStatePath, "utf8"))).toEqual({ cookies: [], origins: [] });
    expect((await stat(storageStatePath)).mode & 0o777).toBe(0o600);
    await handle.close();
  });
});

describe("scheduler failure bound", () => {
  it("exits after bounded consecutive cycle failures", async () => {
    const scrapeOnce = vi.fn(async () => { throw new Error("browser crashed"); });
    const { runForever } = await import("../../scripts/newsfeed/scheduler.js");
    await expect(runForever({
      intervalMs: 1,
      scrapeOnce,
      maxConsecutiveErrors: 2,
      onCycleError: () => {},
    })).rejects.toThrow(/failed 2 consecutive cycles/);
    expect(scrapeOnce).toHaveBeenCalledTimes(2);
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

  it("preserves existing post.tags through merge cycles (no diff and content-changed)", async () => {
    const { mergePosts } = await import("../../scripts/newsfeed/store.js");

    const existing = [
      {
        id: "p1",
        title: "Old title",
        content: "body",
        timestamp: "2025-04-01T11:50:00.000Z",
        timestampMs: new Date("2025-04-01T11:50:00.000Z").getTime(),
        rawImages: ["https://x/y.jpg"],
        images: ["/media/p1.jpg"],
        tags: ["BTC", "crypto", "macro"],
        createdAt: "2025-03-30T00:00:00.000Z",
        updatedAt: "2025-03-30T00:00:00.000Z",
      },
    ];

    // Cycle A — no content diff, scraper has no concept of tags
    const noDiff = mergePosts(
      existing,
      [
        {
          id: "p1",
          title: "Old title",
          content: "body",
          timestamp: "2025-04-01T11:50:00.000Z",
          images: ["https://x/y.jpg"],
        },
      ],
      { now: () => new Date("2025-05-01T00:00:00Z") },
    );
    expect(noDiff.merged[0].tags).toEqual(["BTC", "crypto", "macro"]);

    // Cycle B — title changes (forces the update branch), tags must still survive
    const updated = mergePosts(
      existing,
      [
        {
          id: "p1",
          title: "New title",
          content: "body",
          timestamp: "2025-04-01T11:50:00.000Z",
          images: ["https://x/y.jpg"],
        },
      ],
      { now: () => new Date("2025-05-01T00:00:00Z") },
    );
    expect(updated.changed).toBe(true);
    expect(updated.merged[0].title).toBe("New title");
    expect(updated.merged[0].tags).toEqual(["BTC", "crypto", "macro"]);
    expect(updated.merged[0].createdAt).toBe("2025-03-30T00:00:00.000Z");
    expect(updated.merged[0].updatedAt).toBe("2025-05-01T00:00:00.000Z");
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
  it("fails closed and quarantines malformed existing history", async () => {
    const root = await createTempRoot();
    const postsFile = path.join(root, "posts.json");
    await writeFile(postsFile, "{broken");
    const { loadExistingPosts } = await import("../../scripts/newsfeed/store.js");
    await expect(loadExistingPosts(postsFile)).rejects.toThrow(/quarantined/);
    expect((await readdir(root)).some((name) => name.startsWith("posts.json.corrupt-"))).toBe(true);
  });

  it("concurrent whole-file writers leave one complete valid snapshot", async () => {
    const root = await createTempRoot();
    const dataDir = path.join(root, "data");
    const archiveDir = path.join(dataDir, "archive");
    const postsFile = path.join(dataDir, "posts.json");
    const { persistPosts } = await import("../../scripts/newsfeed/store.js");
    const first = Array.from({ length: 50 }, (_, id) => ({ id: `a-${id}`, timestampMs: id }));
    const second = Array.from({ length: 75 }, (_, id) => ({ id: `b-${id}`, timestampMs: id }));
    await Promise.all([
      persistPosts(first, { dataDir, archiveDir, postsFile }),
      persistPosts(second, { dataDir, archiveDir, postsFile }),
    ]);
    const persisted = JSON.parse(await readFile(postsFile, "utf8"));
    expect([50, 75]).toContain(persisted.length);
    expect(new Set(persisted.map((post: { id: string }) => post.id.split("-")[0])).size).toBe(1);
  });

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

describe("formatCookieHeader", () => {
  it("joins valid cookies into a header string", async () => {
    const { formatCookieHeader } = await import("../../scripts/newsfeed/cdp.js");
    expect(
      formatCookieHeader([
        { name: "P", value: "abc" },
        { name: "U", value: "xyz" },
      ]),
    ).toBe("P=abc; U=xyz");
  });

  it("filters out malformed cookie records and returns empty string for non-arrays", async () => {
    const { formatCookieHeader } = await import("../../scripts/newsfeed/cdp.js");
    expect(formatCookieHeader([{ name: "ok", value: "1" }, { value: "no name" }, null])).toBe("ok=1");
    expect(formatCookieHeader(undefined)).toBe("");
    expect(formatCookieHeader(null)).toBe("");
  });
});

// The downloader decodes and re-encodes every image, so fixtures must be
// complete rasters rather than magic-byte stubs.
const PNG_BYTES = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAACXBIWXMAAAPoAAAD6AG1e1JrAAAADUlEQVR4nGNgYGD4DwABBAEAX+XDSwAAAABJRU5ErkJggg==",
  "base64",
);

describe("createImageDownloader (auth-gated upstream)", () => {
  it("sends Cookie header from getCookieHeader so cookie-gated images succeed", async () => {
    const root = await createTempRoot();
    const mediaDir = path.join(root, "media");
    await mkdir(mediaDir, { recursive: true });

    const { createImageDownloader } = await import("../../scripts/newsfeed/media.js");

    const requests: Array<{ url: string; cookie: string | undefined }> = [];
    const fakeClient = {
      get: async (url: string, options: { headers?: Record<string, string> } = {}) => {
        const cookie = options.headers?.Cookie;
        requests.push({ url, cookie });
        if (!cookie) {
          const err = new Error("Request failed with status code 404") as Error & { response?: unknown };
          err.response = { status: 404 };
          throw err;
        }
        return { status: 200, headers: { "content-type": "image/png" }, data: PNG_BYTES };
      },
    };

    const getCookieHeader = async () => "P=session-token; U=user-token";

    const downloader = createImageDownloader({
      mediaDir,
      client: fakeClient,
      getCookieHeader,
    });

    const result = await downloader.download("cMjrK4n79D", [
      "https://themarketear.com/images/caee42fb8ae49ff83ccb1ad3500fdee5.png",
    ]);

    // Downloader emits absolute URLs (https://media.radon.run/<file>) so
    // the dashboard never hits Next.js's /_next/image with a relative
    // path that 400s on Hetzner. See scripts/newsfeed/media.js.
    expect(result).toHaveLength(1);
    expect(result[0]).toMatch(/^https:\/\/media\.radon\.run\/cmjrk4n79d-01-[a-f0-9]{12}\.png$/);
    expect(requests).toHaveLength(1);
    expect(requests[0].cookie).toBe("P=session-token; U=user-token");

    const onDisk = await readFile(path.join(mediaDir, new URL(result[0]).pathname.slice(1)));
    expect(onDisk.subarray(0, 8).equals(PNG_BYTES.subarray(0, 8))).toBe(true);
  });

  it("falls back to no-cookie request when getCookieHeader is omitted (legacy behavior)", async () => {
    const root = await createTempRoot();
    const mediaDir = path.join(root, "media");
    await mkdir(mediaDir, { recursive: true });

    const { createImageDownloader } = await import("../../scripts/newsfeed/media.js");

    const requests: Array<{ url: string; cookie: string | undefined }> = [];
    const fakeClient = {
      get: async (url: string, options: { headers?: Record<string, string> } = {}) => {
        requests.push({ url, cookie: options.headers?.Cookie });
        return { status: 200, headers: { "content-type": "image/png" }, data: PNG_BYTES };
      },
    };

    const downloader = createImageDownloader({ mediaDir, client: fakeClient });

    const result = await downloader.download("p1", ["https://themarketear.com/images/x.png"]);

    expect(result).toHaveLength(1);
    expect(result[0]).toMatch(/^https:\/\/media\.radon\.run\/p1-01-[a-f0-9]{12}\.png$/);
    expect(requests[0].cookie).toBeUndefined();
  });

  it("refreshes cookies once per download call so a stale jar can be replaced mid-cycle", async () => {
    const root = await createTempRoot();
    const mediaDir = path.join(root, "media");
    await mkdir(mediaDir, { recursive: true });

    const { createImageDownloader } = await import("../../scripts/newsfeed/media.js");

    const cookieValues = ["first", "second"];
    let cookieCalls = 0;
    const getCookieHeader = async () => {
      cookieCalls += 1;
      return cookieValues[Math.min(cookieCalls - 1, cookieValues.length - 1)];
    };

    const seenCookies: string[] = [];
    const fakeClient = {
      get: async (_url: string, options: { headers?: Record<string, string> } = {}) => {
        seenCookies.push(options.headers?.Cookie ?? "");
        return { status: 200, data: Buffer.from("ok") };
      },
    };

    const downloader = createImageDownloader({ mediaDir, client: fakeClient, getCookieHeader });

    await downloader.download("a", ["https://themarketear.com/images/a.png"]);
    await downloader.download("b", ["https://themarketear.com/images/b.png"]);

    expect(cookieCalls).toBe(2);
    expect(seenCookies).toEqual(["first", "second"]);
  });
});

describe("hydrateLocalImages", () => {
  it("clears post.images when rawImages goes empty on re-scrape", async () => {
    // If a post previously had an image but the latest scrape returns no
    // <img>, the persisted `images` array must drop the stale entry — never
    // preserve it from the prior cycle. Scraped state is the source of truth.
    const root = await createTempRoot();
    const mediaDir = path.join(root, "media");
    await mkdir(mediaDir, { recursive: true });

    const { createImageDownloader, hydrateLocalImages } = await import(
      "../../scripts/newsfeed/media.js"
    );

    const downloader = createImageDownloader({
      mediaDir,
      client: { get: async () => ({ status: 200, data: Buffer.from("ok") }) },
    });

    const posts = [
      {
        id: "p1",
        title: "post 1",
        rawImages: [],
        images: ["https://media.radon.run/p1-01.png"],
      },
    ];

    const updated = await hydrateLocalImages(posts, downloader);

    expect(updated).toBe(true);
    expect(posts[0].images).toEqual([]);
  });

  it("leaves post.images empty when rawImages is empty (no prior images)", async () => {
    const root = await createTempRoot();
    const mediaDir = path.join(root, "media");
    await mkdir(mediaDir, { recursive: true });

    const { createImageDownloader, hydrateLocalImages } = await import(
      "../../scripts/newsfeed/media.js"
    );

    const downloader = createImageDownloader({
      mediaDir,
      client: { get: async () => ({ status: 200, data: Buffer.from("ok") }) },
    });

    const posts = [{ id: "p1", title: "post 1", rawImages: [], images: [] }];
    const updated = await hydrateLocalImages(posts, downloader);

    expect(updated).toBe(false);
    expect(posts[0].images).toEqual([]);
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
    // schema.image is NOT honoured — only <img> tags inside the article
    // subtree count. This article has no <img>, so images stays empty.
    expect(wellFormed.images).toEqual([]);

    const fallback = parsed.items.find((it: any) => it.id === "article-67890");
    expect(fallback).toBeDefined();
    expect(fallback.title).toBe("Apple slips on supply chain");
    expect(fallback.timestamp).toBe("2025-01-15T11:00:00Z");
    expect(fallback.content).toContain("Apple down 3%");
    expect(fallback.images).toContain("https://themarketear.com/uploads/aapl.jpg");
  });

  it("decodes HTML entities in JSON-LD headline and description", async () => {
    const { buildExtractionExpression, parsePayload } = await import(
      "../../scripts/newsfeed/extract.js"
    );

    const ld = JSON.stringify({
      "@type": "Article",
      headline: "European Utilities&#39; re-rating",
      description: "AT&amp;T &amp; peers &mdash; &quot;quality&quot; growth",
      datePublished: "2026-04-28T07:00:00Z",
    });

    const fixture = `
      <article class="post" id="entity-1">
        <script type="application/ld+json">${ld}</script>
        <time datetime="2026-04-28T07:00:00Z">today</time>
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
    const item = parsed.items.find((it: any) => it.id === "entity-1");
    expect(item).toBeDefined();
    expect(item.title).toBe("European Utilities' re-rating");
    expect(item.content).toBe('AT&T & peers — "quality" growth');
  });

  it("returns images: [] when article has no <img> tag, even if JSON-LD schema.image is set", async () => {
    // themarketear.com puts `assets/images/generic.png` (a placeholder) into
    // JSON-LD `schema.image` for image-less posts. The DOM `<article>` itself
    // contains no `<img>` tag in those cases. The scraper MUST trust the DOM
    // and ignore the schema fallback — otherwise every image-less post inherits
    // whatever bytes happen to live behind that placeholder URL, and the
    // downloader cache pins them all to the same local filename.
    const { buildExtractionExpression, parsePayload } = await import(
      "../../scripts/newsfeed/extract.js"
    );

    const fixture = `
      <article class="post" id="no-image-post">
        <script type="application/ld+json">
          {"@type":"Article","headline":"The Fed volatility trade","datePublished":"2026-05-21T20:00:00Z","image":["https://themarketear.com/assets/images/generic.png"]}
        </script>
        <h2 class="title">The Fed volatility trade</h2>
        <div class="body"><div class="content">Pure text post, no chart.</div></div>
        <time datetime="2026-05-21T20:00:00Z">today</time>
      </article>
      <article class="post" id="real-image-post">
        <script type="application/ld+json">
          {"@type":"Article","headline":"Asia stimulus","datePublished":"2026-05-21T19:00:00Z","image":["https://themarketear.com/assets/images/generic.png"]}
        </script>
        <h2 class="title">Asia stimulus</h2>
        <div class="body"><div class="content">With chart.</div></div>
        <time datetime="2026-05-21T19:00:00Z">today</time>
        <img src="https://themarketear.com/images/asia-chart.png" />
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

    const noImage = parsed.items.find((it: any) => it.id === "no-image-post");
    expect(noImage).toBeDefined();
    expect(noImage.images).toEqual([]);

    const withImage = parsed.items.find((it: any) => it.id === "real-image-post");
    expect(withImage).toBeDefined();
    expect(withImage.images).toEqual(["https://themarketear.com/images/asia-chart.png"]);
  });

  it("does not bleed images across sibling articles in the DOM", async () => {
    // Defensive regression for the EMB-chart-on-every-post bug. Each post's
    // images must come from that post's own <article> subtree, never from
    // a sibling article elsewhere on the page.
    const { buildExtractionExpression, parsePayload } = await import(
      "../../scripts/newsfeed/extract.js"
    );

    const fixture = `
      <article class="post" id="with-chart">
        <h2 class="title">Has a chart</h2>
        <div class="body"><div class="content">x</div></div>
        <time datetime="2026-05-21T20:00:00Z"></time>
        <img src="https://themarketear.com/images/emb-chart.png" />
      </article>
      <article class="post" id="no-chart-1">
        <h2 class="title">No chart 1</h2>
        <div class="body"><div class="content">y</div></div>
        <time datetime="2026-05-21T19:30:00Z"></time>
      </article>
      <article class="post" id="no-chart-2">
        <h2 class="title">No chart 2</h2>
        <div class="body"><div class="content">z</div></div>
        <time datetime="2026-05-21T19:00:00Z"></time>
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

    expect(parsed.items.find((it: any) => it.id === "with-chart").images).toEqual([
      "https://themarketear.com/images/emb-chart.png",
    ]);
    expect(parsed.items.find((it: any) => it.id === "no-chart-1").images).toEqual([]);
    expect(parsed.items.find((it: any) => it.id === "no-chart-2").images).toEqual([]);
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
