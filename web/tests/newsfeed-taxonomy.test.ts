import { mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";

let projectRoot: string | null = null;

async function createProjectRoot() {
  projectRoot = await mkdtemp(path.join(tmpdir(), "radon-taxonomy-test-"));
  await mkdir(path.join(projectRoot, "data"), { recursive: true });
  return projectRoot;
}

afterEach(async () => {
  if (projectRoot) {
    await rm(projectRoot, { recursive: true, force: true });
    projectRoot = null;
  }
});

describe("appendTagsToTaxonomy", () => {
  it("creates the file with seed defaults when missing and appends candidate tags", async () => {
    const root = await createProjectRoot();
    const { appendTagsToTaxonomy, loadTaxonomy } = await import(
      "../../scripts/newsfeed/taxonomy.js"
    );

    const additions = await appendTagsToTaxonomy(root, ["puts", "options", "positioning"]);
    expect(additions).toEqual(["puts", "options", "positioning"]);

    const after = await loadTaxonomy(root);
    expect(after.tags).toEqual(["puts", "options", "positioning"]);
  });

  it("returns only the genuinely-new tags when some already exist", async () => {
    const root = await createProjectRoot();
    const file = path.join(root, "data", "tag_taxonomy.json");
    await writeFile(
      file,
      JSON.stringify({ version: 1, tags: ["BTC", "vol", "positioning"] }, null, 2),
    );

    const { appendTagsToTaxonomy, loadTaxonomy } = await import(
      "../../scripts/newsfeed/taxonomy.js"
    );

    const additions = await appendTagsToTaxonomy(root, ["BTC", "puts", "options"]);
    expect(additions).toEqual(["puts", "options"]);

    const after = await loadTaxonomy(root);
    expect(after.tags).toEqual(["BTC", "vol", "positioning", "puts", "options"]);
  });

  it("is a no-op when every candidate is already present", async () => {
    const root = await createProjectRoot();
    const file = path.join(root, "data", "tag_taxonomy.json");
    await writeFile(file, JSON.stringify({ version: 1, tags: ["BTC", "vol"] }, null, 2));

    const { appendTagsToTaxonomy } = await import("../../scripts/newsfeed/taxonomy.js");
    const additions = await appendTagsToTaxonomy(root, ["BTC", "vol"]);
    expect(additions).toEqual([]);

    // File contents unchanged.
    const raw = await readFile(file, "utf8");
    expect(JSON.parse(raw).tags).toEqual(["BTC", "vol"]);
  });

  it("dedupes case-insensitively against the existing taxonomy", async () => {
    const root = await createProjectRoot();
    const file = path.join(root, "data", "tag_taxonomy.json");
    await writeFile(file, JSON.stringify({ version: 1, tags: ["BTC", "VOL"] }, null, 2));

    const { appendTagsToTaxonomy, loadTaxonomy } = await import(
      "../../scripts/newsfeed/taxonomy.js"
    );

    // Lowercase candidates should NOT be re-appended — the existing canonical wins.
    const additions = await appendTagsToTaxonomy(root, ["btc", "Vol", "PUTS"]);
    expect(additions).toEqual(["PUTS"]);

    const after = await loadTaxonomy(root);
    expect(after.tags).toEqual(["BTC", "VOL", "PUTS"]);
  });

  it("survives concurrent writers by re-reading before each write", async () => {
    const root = await createProjectRoot();
    const file = path.join(root, "data", "tag_taxonomy.json");
    await writeFile(file, JSON.stringify({ version: 1, tags: [] }, null, 2));

    const { appendTagsToTaxonomy, loadTaxonomy } = await import(
      "../../scripts/newsfeed/taxonomy.js"
    );

    await Promise.all([
      appendTagsToTaxonomy(root, ["puts", "options"]),
      appendTagsToTaxonomy(root, ["calls", "options"]),
      appendTagsToTaxonomy(root, ["positioning", "puts"]),
    ]);

    const after = await loadTaxonomy(root);
    // Whichever order the writes interleave, the final set must contain every unique tag.
    expect(new Set(after.tags)).toEqual(new Set(["puts", "options", "calls", "positioning"]));
  });
});
