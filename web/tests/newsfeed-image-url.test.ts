import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { mkdir, mkdtemp, readFile, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";

// Image-URL contract: the newsfeed scraper writes ABSOLUTE URLs rooted at
// https://media.radon.run/<file>. Consumers (Turso writer, vision tagger,
// dashboard) all rely on that invariant. Relative `/media/<file>` paths
// 400 from Next.js's image optimiser on app.radon.run because Hetzner has
// no /media/* static route.

async function tempDir() {
  return mkdtemp(path.join(tmpdir(), "radon-newsfeed-img-"));
}

// The downloader refuses anything that is not a real raster image before it
// writes into the publicly served media dir, so fixtures must carry PNG magic
// bytes. See scripts/newsfeed/media.js:looksLikeImage.
const PNG_BYTES = Buffer.from([
  0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0x00, 0x00, 0x00, 0x0d,
]);

describe("media.js — absolutizeMediaUrl", () => {
  it("rewrites /media/<file> to https://media.radon.run/<file>", async () => {
    const { absolutizeMediaUrl, MEDIA_ORIGIN } = await import(
      "../../scripts/newsfeed/media.js"
    );
    expect(absolutizeMediaUrl("/media/abc.png")).toBe(`${MEDIA_ORIGIN}/abc.png`);
  });

  it("is idempotent on already-absolute media URLs", async () => {
    const { absolutizeMediaUrl, MEDIA_ORIGIN } = await import(
      "../../scripts/newsfeed/media.js"
    );
    const url = `${MEDIA_ORIGIN}/abc.png`;
    expect(absolutizeMediaUrl(url)).toBe(url);
  });

  it("leaves foreign absolute URLs untouched", async () => {
    const { absolutizeMediaUrl } = await import("../../scripts/newsfeed/media.js");
    const url = "https://cdn.themarketear.com/images/foo.png";
    expect(absolutizeMediaUrl(url)).toBe(url);
  });

  it("returns non-string inputs unchanged", async () => {
    const { absolutizeMediaUrl } = await import("../../scripts/newsfeed/media.js");
    expect(absolutizeMediaUrl(null)).toBe(null);
    expect(absolutizeMediaUrl(undefined)).toBe(undefined);
    expect(absolutizeMediaUrl(42)).toBe(42);
  });
});

describe("media.js — createImageDownloader produces absolute URLs", () => {
  it("returns https://media.radon.run/<slug>-NN.<ext> for a freshly downloaded image", async () => {
    const root = await tempDir();
    const mediaDir = path.join(root, "media");
    await mkdir(mediaDir, { recursive: true });

    const { createImageDownloader, MEDIA_ORIGIN } = await import(
      "../../scripts/newsfeed/media.js"
    );

    const fakeClient = {
      get: async () => ({ status: 200, headers: { "content-type": "image/png" }, data: PNG_BYTES }),
    };

    const downloader = createImageDownloader({ mediaDir, client: fakeClient });
    const result = await downloader.download("cdcdxbktba", [
      "https://themarketear.com/images/cdcdxbktba.png",
    ]);

    expect(result).toEqual([`${MEDIA_ORIGIN}/cdcdxbktba-01.png`]);
    expect(result[0].startsWith("https://media.radon.run/")).toBe(true);
  });

  it("returns absolute URLs even when the file already exists on disk (cache hit)", async () => {
    const root = await tempDir();
    const mediaDir = path.join(root, "media");
    await mkdir(mediaDir, { recursive: true });
    // Pre-populate so the downloader hits the existing-file branch.
    await writeFile(path.join(mediaDir, "abc-01.png"), "PNG");

    const { createImageDownloader, MEDIA_ORIGIN } = await import(
      "../../scripts/newsfeed/media.js"
    );

    let getCalls = 0;
    const fakeClient = {
      get: async () => {
        getCalls += 1;
        return { status: 200, data: Buffer.from("PNG") };
      },
    };

    const downloader = createImageDownloader({ mediaDir, client: fakeClient });
    const result = await downloader.download("abc", [
      "https://themarketear.com/images/abc.png",
    ]);

    expect(getCalls).toBe(0);
    expect(result).toEqual([`${MEDIA_ORIGIN}/abc-01.png`]);
  });
});

describe("store.js — normalisePostImageUrls boundary guard", () => {
  it("rewrites legacy relative URLs before persistence", async () => {
    const { __normalisePostImageUrls } = await import(
      "../../scripts/newsfeed/store.js"
    );
    const out = __normalisePostImageUrls({
      id: "p1",
      images: ["/media/foo.png", "/media/bar.png"],
    });
    expect(out.images).toEqual([
      "https://media.radon.run/foo.png",
      "https://media.radon.run/bar.png",
    ]);
  });

  it("preserves an already-absolute post object reference (no rewrite)", async () => {
    const { __normalisePostImageUrls } = await import(
      "../../scripts/newsfeed/store.js"
    );
    const input = {
      id: "p1",
      images: ["https://media.radon.run/foo.png"],
    };
    const out = __normalisePostImageUrls(input);
    expect(out).toBe(input); // identity — nothing to do
  });

  it("drops malformed entries it cannot absolutise and logs a warning", async () => {
    const warns: unknown[] = [];
    const originalWarn = console.warn;
    console.warn = (...args: unknown[]) => warns.push(args);
    try {
      const { __normalisePostImageUrls } = await import(
        "../../scripts/newsfeed/store.js"
      );
      const out = __normalisePostImageUrls({
        id: "p1",
        images: ["https://media.radon.run/foo.png", "weird-bare-string"],
      });
      expect(out.images).toEqual(["https://media.radon.run/foo.png"]);
      expect(warns.length).toBe(1);
    } finally {
      console.warn = originalWarn;
    }
  });

  it("returns posts without images untouched", async () => {
    const { __normalisePostImageUrls } = await import(
      "../../scripts/newsfeed/store.js"
    );
    const input = { id: "p1" };
    expect(__normalisePostImageUrls(input)).toBe(input);
  });
});

describe("store.js — persistPosts writes absolute URLs to disk", () => {
  it("rewrites relative URLs in the persisted JSON", async () => {
    const root = await tempDir();
    const dataDir = path.join(root, "data");
    const archiveDir = path.join(dataDir, "archive");
    const mediaDir = path.join(root, "media");
    const postsFile = path.join(dataDir, "posts.json");
    await mkdir(dataDir, { recursive: true });
    await mkdir(archiveDir, { recursive: true });
    await mkdir(mediaDir, { recursive: true });

    const { persistPosts } = await import("../../scripts/newsfeed/store.js");

    await persistPosts(
      [
        {
          id: "p1",
          title: "t",
          timestamp: "2026-05-19T00:00:00Z",
          images: ["/media/p1-01.png"],
        },
      ],
      { dataDir, archiveDir, mediaDir, postsFile },
    );

    const out = JSON.parse(await readFile(postsFile, "utf8"));
    expect(out[0].images).toEqual(["https://media.radon.run/p1-01.png"]);
  });
});

describe("vision_tagger.js — resolveImageAbsolutePath handles both URL forms", () => {
  it("resolves https://media.radon.run/<file> to publicRoot/media/<file>", async () => {
    const { __resolveImageAbsolutePath } = await import(
      "../../scripts/newsfeed/vision_tagger.js"
    );
    expect(
      __resolveImageAbsolutePath(
        { images: ["https://media.radon.run/abc.png"] },
        "/root",
      ),
    ).toBe("/root/media/abc.png");
  });

  it("still handles legacy /media/<file> paths (backwards compat)", async () => {
    const { __resolveImageAbsolutePath } = await import(
      "../../scripts/newsfeed/vision_tagger.js"
    );
    expect(
      __resolveImageAbsolutePath({ images: ["/media/abc.png"] }, "/root"),
    ).toBe("/root/media/abc.png");
  });

  it("still handles bare media/<file> (no leading slash)", async () => {
    const { __resolveImageAbsolutePath } = await import(
      "../../scripts/newsfeed/vision_tagger.js"
    );
    expect(
      __resolveImageAbsolutePath({ images: ["media/abc.png"] }, "/root"),
    ).toBe("/root/media/abc.png");
  });

  it("returns null when the URL is malformed", async () => {
    const { __resolveImageAbsolutePath } = await import(
      "../../scripts/newsfeed/vision_tagger.js"
    );
    expect(
      __resolveImageAbsolutePath({ images: ["http://"] }, "/root"),
    ).toBeNull();
  });
});

describe("scripts/db/writer.js — absolutizeMedia idempotent guard", () => {
  it("rewrites relative input (defence-in-depth for any legacy upstream)", async () => {
    // The writer module reads TURSO_DB_URL eagerly, so test the helper via
    // upsertPosts against an in-memory libSQL — same pattern the existing
    // dual-write test uses.
    const { createClient } = await import("@libsql/client");
    const db = createClient({ url: ":memory:" });
    await db.execute(`
      CREATE TABLE IF NOT EXISTS posts (
        id TEXT PRIMARY KEY, title TEXT NOT NULL, content TEXT,
        timestamp TEXT NOT NULL, images TEXT, raw_images TEXT,
        tags TEXT, tags_text TEXT, tags_vision TEXT,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL
      )
    `);

    const writer = await import("../../scripts/db/writer.js");
    writer.__setDbForTests(db);

    await writer.upsertPosts([
      {
        id: "legacy",
        title: "x",
        timestamp: "2026-05-19T00:00:00Z",
        images: ["/media/legacy.png"],
      },
    ]);

    const result = await db.execute("SELECT images FROM posts WHERE id = 'legacy'");
    const row = result.rows[0] as unknown as { images: string };
    expect(JSON.parse(row.images)).toEqual([
      "https://media.radon.run/legacy.png",
    ]);

    writer.__resetDbForTests();
    db.close();
  });
});
