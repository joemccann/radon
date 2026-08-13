import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";

let tempRoot: string;

beforeEach(async () => {
  tempRoot = await mkdtemp(path.join(tmpdir(), "radon-newsfeed-migrate-"));
});

afterEach(async () => {
  if (tempRoot) {
    await rm(tempRoot, { recursive: true, force: true });
  }
});

describe("migratePosts — pure transform", () => {
  it("rewrites relative /media/<file> to absolute", async () => {
    const { migratePosts } = await import(
      "../../scripts/newsfeed/migrate_relative_image_urls.js"
    );
    const { posts, rewrites } = migratePosts([
      { id: "a", images: ["/media/foo.png", "/media/bar.png"] },
      { id: "b", images: ["https://media.radon.run/baz.png"] },
    ]);
    expect(rewrites).toBe(2);
    expect(posts[0].images).toEqual([
      "https://media.radon.run/foo.png",
      "https://media.radon.run/bar.png",
    ]);
    expect(posts[1].images).toEqual(["https://media.radon.run/baz.png"]);
  });

  it("is idempotent — second pass rewrites 0", async () => {
    const { migratePosts } = await import(
      "../../scripts/newsfeed/migrate_relative_image_urls.js"
    );
    const first = migratePosts([{ id: "a", images: ["/media/foo.png"] }]);
    const second = migratePosts(first.posts);
    expect(first.rewrites).toBe(1);
    expect(second.rewrites).toBe(0);
    expect(second.posts[0].images).toEqual(["https://media.radon.run/foo.png"]);
  });

  it("leaves foreign absolute URLs untouched", async () => {
    const { migratePosts } = await import(
      "../../scripts/newsfeed/migrate_relative_image_urls.js"
    );
    const { posts, rewrites } = migratePosts([
      { id: "a", images: ["https://cdn.themarketear.com/foo.png"] },
    ]);
    expect(rewrites).toBe(0);
    expect(posts[0].images).toEqual(["https://cdn.themarketear.com/foo.png"]);
  });

  it("handles posts without images", async () => {
    const { migratePosts } = await import(
      "../../scripts/newsfeed/migrate_relative_image_urls.js"
    );
    const { posts, rewrites } = migratePosts([
      { id: "a", title: "no images" },
      { id: "b", images: [] },
    ]);
    expect(rewrites).toBe(0);
    expect(posts).toHaveLength(2);
  });

  it("returns empty result for non-array input", async () => {
    const { migratePosts } = await import(
      "../../scripts/newsfeed/migrate_relative_image_urls.js"
    );
    // @ts-expect-error testing defensive handling
    expect(migratePosts(null)).toEqual({ posts: [], rewrites: 0 });
  });
});

describe("runMigration — file IO", () => {
  it("dry-run reports counts but does NOT write the file", async () => {
    const dataDir = path.join(tempRoot, "data");
    const archiveDir = path.join(dataDir, "archive");
    await mkdir(archiveDir, { recursive: true });
    const postsFile = path.join(dataDir, "posts.json");
    const before = JSON.stringify(
      [{ id: "a", title: "t", timestamp: "2026-05-19T00:00:00Z", images: ["/media/foo.png"] }],
      null,
      2,
    );
    await writeFile(postsFile, before);

    const { runMigration } = await import(
      "../../scripts/newsfeed/migrate_relative_image_urls.js"
    );
    const logs: string[] = [];
    const { totalRewrites } = await runMigration({
      postsFile,
      archiveDir,
      apply: false,
      log: (msg) => logs.push(msg),
    });

    expect(totalRewrites).toBe(1);
    // File unchanged.
    expect(await readFile(postsFile, "utf8")).toBe(before);
    // Log mentions dry-run.
    expect(logs.some((l) => /dry-run/i.test(l))).toBe(true);
  });

  it("--apply writes the rewritten file; re-running reports 0", async () => {
    const dataDir = path.join(tempRoot, "data");
    const archiveDir = path.join(dataDir, "archive");
    await mkdir(archiveDir, { recursive: true });
    const postsFile = path.join(dataDir, "posts.json");
    await writeFile(
      postsFile,
      JSON.stringify(
        [
          {
            id: "a",
            title: "t",
            timestamp: "2026-05-19T00:00:00Z",
            images: ["/media/foo.png", "/media/bar.png"],
          },
        ],
        null,
        2,
      ),
    );

    const { runMigration } = await import(
      "../../scripts/newsfeed/migrate_relative_image_urls.js"
    );
    const first = await runMigration({
      postsFile,
      archiveDir,
      apply: true,
      log: () => {},
    });
    expect(first.totalRewrites).toBe(2);

    const after = JSON.parse(await readFile(postsFile, "utf8"));
    expect(after[0].images).toEqual([
      "https://media.radon.run/foo.png",
      "https://media.radon.run/bar.png",
    ]);
    const backup = JSON.parse(await readFile(`${postsFile}.bak`, "utf8"));
    expect(backup[0].images).toEqual(["/media/foo.png", "/media/bar.png"]);

    // Idempotent — second pass is a no-op.
    const second = await runMigration({
      postsFile,
      archiveDir,
      apply: true,
      log: () => {},
    });
    expect(second.totalRewrites).toBe(0);
  });

  it("walks archive files too", async () => {
    const dataDir = path.join(tempRoot, "data");
    const archiveDir = path.join(dataDir, "archive");
    await mkdir(archiveDir, { recursive: true });
    const postsFile = path.join(dataDir, "posts.json");

    await writeFile(
      postsFile,
      JSON.stringify(
        [{ id: "live", title: "t", timestamp: "2026-05-19T00:00:00Z", images: ["/media/live.png"] }],
        null,
        2,
      ),
    );
    await writeFile(
      path.join(archiveDir, "posts-2026-05-01.json"),
      JSON.stringify(
        [
          { id: "old1", title: "t", timestamp: "2026-05-01T00:00:00Z", images: ["/media/old1.png"] },
          { id: "old2", title: "t", timestamp: "2026-05-01T00:00:00Z", images: ["/media/old2.png"] },
        ],
        null,
        2,
      ),
    );

    const { runMigration } = await import(
      "../../scripts/newsfeed/migrate_relative_image_urls.js"
    );
    const { totalRewrites } = await runMigration({
      postsFile,
      archiveDir,
      apply: true,
      log: () => {},
    });
    expect(totalRewrites).toBe(3); // 1 live + 2 archived
  });

  it("handles absent posts file gracefully", async () => {
    const dataDir = path.join(tempRoot, "data");
    const archiveDir = path.join(dataDir, "archive");
    await mkdir(archiveDir, { recursive: true });
    const postsFile = path.join(dataDir, "posts.json"); // never created

    const { runMigration } = await import(
      "../../scripts/newsfeed/migrate_relative_image_urls.js"
    );
    const { totalRewrites } = await runMigration({
      postsFile,
      archiveDir,
      apply: false,
      log: () => {},
    });
    expect(totalRewrites).toBe(0);
  });
});
