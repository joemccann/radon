import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";

let tempRoot: string;

beforeEach(async () => {
  tempRoot = await mkdtemp(path.join(tmpdir(), "radon-newsfeed-scrub-"));
});

afterEach(async () => {
  if (tempRoot) {
    await rm(tempRoot, { recursive: true, force: true });
  }
});

describe("scrubPosts — pure transform", () => {
  it("clears rawImages and images for posts whose rawImages contain the generic placeholder", async () => {
    const { scrubPosts } = await import(
      "../../scripts/newsfeed/scrub_generic_image_attributions.js"
    );
    const { posts, scrubbed } = scrubPosts([
      {
        id: "a",
        rawImages: ["https://themarketear.com/assets/images/generic.png"],
        images: ["https://media.radon.run/a-01.png"],
      },
      {
        id: "b",
        rawImages: ["https://themarketear.com/images/real-chart.png"],
        images: ["https://media.radon.run/b-01.png"],
      },
      { id: "c", rawImages: [], images: [] },
    ]);
    expect(scrubbed).toBe(1);
    expect(posts[0].rawImages).toEqual([]);
    expect(posts[0].images).toEqual([]);
    expect(posts[1].rawImages).toEqual(["https://themarketear.com/images/real-chart.png"]);
    expect(posts[1].images).toEqual(["https://media.radon.run/b-01.png"]);
    expect(posts[2].rawImages).toEqual([]);
  });

  it("is idempotent — re-running on scrubbed posts produces zero rewrites", async () => {
    const { scrubPosts } = await import(
      "../../scripts/newsfeed/scrub_generic_image_attributions.js"
    );
    const input = [
      {
        id: "a",
        rawImages: ["https://themarketear.com/assets/images/generic.png"],
        images: ["https://media.radon.run/a-01.png"],
      },
    ];
    const first = scrubPosts(input);
    expect(first.scrubbed).toBe(1);
    const second = scrubPosts(first.posts);
    expect(second.scrubbed).toBe(0);
  });

  it("handles non-array input safely", async () => {
    const { scrubPosts } = await import(
      "../../scripts/newsfeed/scrub_generic_image_attributions.js"
    );
    expect(scrubPosts(null as unknown as never[])).toEqual({ posts: [], scrubbed: 0 });
    expect(scrubPosts(undefined as unknown as never[])).toEqual({ posts: [], scrubbed: 0 });
  });
});

describe("runScrub — file-level", () => {
  it("writes scrubbed posts to disk under --apply", async () => {
    const { runScrub } = await import(
      "../../scripts/newsfeed/scrub_generic_image_attributions.js"
    );

    const dataDir = path.join(tempRoot, "data");
    await mkdir(dataDir, { recursive: true });
    const postsFile = path.join(dataDir, "posts.json");
    const archiveDir = path.join(dataDir, "archive");

    await writeFile(
      postsFile,
      JSON.stringify([
        {
          id: "bad",
          rawImages: ["https://themarketear.com/assets/images/generic.png"],
          images: ["https://media.radon.run/x-01.png"],
        },
        {
          id: "good",
          rawImages: ["https://themarketear.com/images/legit.png"],
          images: ["https://media.radon.run/good-01.png"],
        },
      ]),
    );

    const { totalScrubbed } = await runScrub({
      postsFile,
      archiveDir,
      apply: true,
      log: () => {},
    });

    expect(totalScrubbed).toBe(1);
    const after = JSON.parse(await readFile(postsFile, "utf8"));
    expect(after[0].images).toEqual([]);
    expect(after[0].rawImages).toEqual([]);
    expect(after[1].images).toEqual(["https://media.radon.run/good-01.png"]);
    const backup = JSON.parse(await readFile(`${postsFile}.bak`, "utf8"));
    expect(backup[0].images).toEqual(["https://media.radon.run/x-01.png"]);
  });

  it("dry-run leaves files untouched", async () => {
    const { runScrub } = await import(
      "../../scripts/newsfeed/scrub_generic_image_attributions.js"
    );

    const dataDir = path.join(tempRoot, "data");
    await mkdir(dataDir, { recursive: true });
    const postsFile = path.join(dataDir, "posts.json");
    const archiveDir = path.join(dataDir, "archive");
    const original = JSON.stringify([
      {
        id: "bad",
        rawImages: ["https://themarketear.com/assets/images/generic.png"],
        images: ["https://media.radon.run/x-01.png"],
      },
    ]);
    await writeFile(postsFile, original);

    const { totalScrubbed } = await runScrub({
      postsFile,
      archiveDir,
      apply: false,
      log: () => {},
    });

    expect(totalScrubbed).toBe(1);
    expect(await readFile(postsFile, "utf8")).toBe(original);
  });
});
