import { mkdtemp, mkdir, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import vm from "node:vm";
import { JSDOM } from "jsdom";
import { afterEach, describe, expect, it } from "vitest";

const RAW_A = "https://themarketear.com/images/a.png";
const RAW_B = "https://themarketear.com/images/b.png";
const LOCAL_A = "https://media.radon.run/a.png";
const PNG = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAACXBIWXMAAAPoAAAD6AG1e1JrAAAADUlEQVR4nGNgYGD4DwABBAEAX+XDSwAAAABJRU5ErkJggg==", "base64");
let root: string | undefined;
afterEach(async () => { if (root) await rm(root, { recursive: true, force: true }); root = undefined; });

function post(overrides: Record<string, unknown> = {}) {
  return { id: "adoption", title: "Adoption slows", content: "AI adoption", timestamp: "2026-09-10T08:50:00.000Z", images: [RAW_A], ...overrides };
}

describe("Market Ear image source extraction", () => {
  it("extracts explicit per-chart sources, decodes entities, and keeps uncited images uncited", async () => {
    const { buildExtractionExpression } = await import("../../scripts/newsfeed/extract.js");
    const dom = new JSDOM(`<article class="post" id="adoption"><h2 class="title">Adoption slows</h2><time datetime="2026-09-10T08:50:00Z"></time>
      <div class="body"><p class="content">Source: body text is not an image credit.</p>
        <div class="figure"><img src="/images/a.png"><div class="image-caption">Source: <a>Ramp</a></div></div>
        <figure><img data-src="/images/b.png"><figcaption>Source: Goldman Sachs &amp; Co.</figcaption></figure>
        <div class="figure"><img src="/images/c.png"><div class="image-caption">AI adoption chart</div></div>
        <div class="figure"><img src="/images/d.png"><div class="image-caption">Source: &nbsp;</div></div>
        <img src="/images/e.png">
      </div></article>`);
    const raw = vm.runInNewContext(buildExtractionExpression(), { document: dom.window.document, URL });
    const extracted = JSON.parse(raw).items[0];
    expect(extracted.imageSources).toEqual({ [RAW_A]: "Ramp", [RAW_B]: "Goldman Sachs & Co." });
    expect(extracted.images).toHaveLength(5);
  });

  it("does not borrow a sibling figure's source or use JSON-LD images", async () => {
    const { buildExtractionExpression } = await import("../../scripts/newsfeed/extract.js");
    const dom = new JSDOM(`<article class="post" id="x"><h2 class="title">Chart</h2><time datetime="2026-09-10T08:50:00Z"></time>
      <script type="application/ld+json">{"image":"/images/generic.png"}</script>
      <div class="figure"><img src="/images/a.png"><div class="figure"><img src="/images/b.png"><div class="image-caption">Source: Ramp</div></div></div>
      </article>`);
    const extracted = JSON.parse(vm.runInNewContext(buildExtractionExpression(), { document: dom.window.document, URL })).items[0];
    expect(extracted.imageSources).toEqual({ [RAW_B]: "Ramp" });
    expect(extracted.images).toEqual([RAW_A, RAW_B]);
  });
});

describe("Market Ear source storage", () => {
  it("updates source-only changes and removals while preserving tags and untouched older posts", async () => {
    const { mergePosts } = await import("../../scripts/newsfeed/store.js");
    const existing = post({ images: [LOCAL_A], rawImages: [RAW_A], rawImageSources: { [RAW_A]: "Old" }, imageSources: { [LOCAL_A]: "Old" }, tags: ["AI"], createdAt: "2026-09-01T00:00:00Z" });
    const older = post({ id: "older", imageSources: { [RAW_A]: "Older" } });
    const changed = mergePosts([existing, older], [post({ imageSources: { [RAW_A]: "Ramp" } })]);
    expect(changed.changed).toBe(true);
    expect(changed.merged.find((p: any) => p.id === "adoption")).toMatchObject({ rawImageSources: { [RAW_A]: "Ramp" }, tags: ["AI"], createdAt: existing.createdAt });
    expect(changed.merged.find((p: any) => p.id === "older")).toEqual(older);
    const removed = mergePosts(changed.merged, [post({ imageSources: {} })]);
    expect(removed.changed).toBe(true);
    expect(removed.merged.find((p: any) => p.id === "adoption").rawImageSources).toEqual({});
  });

  it("remaps citations by download slot when an earlier chart fails, and clears removed citations", async () => {
    root = await mkdtemp(path.join(tmpdir(), "radon-sources-"));
    const { createImageDownloader, hydrateLocalImages } = await import("../../scripts/newsfeed/media.js");
    await mkdir(path.join(root, "media"));
    const downloader = createImageDownloader({ mediaDir: path.join(root, "media"), client: { get: async (url: string) => {
      if (url === RAW_A) throw new Error("404");
      return { status: 200, headers: { "content-type": "image/png" }, data: PNG };
    } } });
    const item = post({ images: [], rawImages: [RAW_A, RAW_B], rawImageSources: { [RAW_A]: "Wrong", [RAW_B]: "Ramp" } }) as any;
    expect(await hydrateLocalImages([item], downloader)).toBe(true);
    expect(item.images).toHaveLength(1);
    expect(item.imageSources).toEqual({ [item.images[0]]: "Ramp" });
    expect(await hydrateLocalImages([item], downloader)).toBe(false);
    item.rawImageSources = {};
    expect(await hydrateLocalImages([item], downloader)).toBe(true);
    expect(item.imageSources).toEqual({});
    item.imageSources = { [item.images[0]]: "Stale" };
    item.rawImages = [];
    expect(await hydrateLocalImages([item], downloader)).toBe(true);
    expect(item.imageSources).toEqual({});
  });

  it("persists sources with normalized URLs and drops credits for absent images", async () => {
    root = await mkdtemp(path.join(tmpdir(), "radon-sources-"));
    const { persistPosts, loadExistingPosts } = await import("../../scripts/newsfeed/store.js");
    const postsFile = path.join(root, "posts.json");
    await persistPosts([post({ images: ["/media/a.png"], rawImages: [RAW_A], rawImageSources: { [RAW_A]: "Ramp" }, imageSources: { "/media/a.png": "Ramp", missing: "Wrong" } })], { dataDir: root, archiveDir: path.join(root, "archive"), postsFile });
    const stored = JSON.parse(await readFile(postsFile, "utf8"))[0];
    expect(stored.imageSources).toEqual({ [LOCAL_A]: "Ramp" });
    expect((await loadExistingPosts(postsFile))[0].rawImageSources).toEqual({ [RAW_A]: "Ramp" });
  });
});
