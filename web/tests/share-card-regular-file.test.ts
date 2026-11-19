import { describe, it, expect, beforeAll, afterAll } from "vitest";
import { mkdtempSync, mkdirSync, rmSync, symlinkSync, writeFileSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { readShareCard, readRegularShareCardFile, SHARE_CARDS_DIR } from "@/lib/shareReportPath";

// The share-card directory is a shared read-write mount. A card name that passes the
// lexical allowlist must still only ever yield the bytes of a regular file sitting
// in that directory: a link planted under a valid card name is refused, never followed.

let tmp: string;
let outside: string;

beforeAll(() => {
  tmp = mkdtempSync(join(tmpdir(), "share-cards-"));
  outside = join(tmp, "outside.txt");
  writeFileSync(outside, "not a card", "utf-8");
  writeFileSync(join(tmp, "tweet-gex-2026-11-19.html"), "<p>card</p>", "utf-8");
  symlinkSync(outside, join(tmp, "tweet-gex-2026-11-18.html"));
  mkdirSync(join(tmp, "tweet-gex-2026-11-17.html"));
});

afterAll(() => {
  rmSync(tmp, { recursive: true, force: true });
});

describe("readRegularShareCardFile", () => {
  it("serves a regular file", async () => {
    await expect(readRegularShareCardFile(join(tmp, "tweet-gex-2026-11-19.html"))).resolves.toBe("<p>card</p>");
  });

  it("refuses a link even when its target is readable", async () => {
    await expect(readRegularShareCardFile(join(tmp, "tweet-gex-2026-11-18.html"))).resolves.toBeNull();
  });

  it("refuses a non-regular entry and a missing file", async () => {
    await expect(readRegularShareCardFile(join(tmp, "tweet-gex-2026-11-17.html"))).resolves.toBeNull();
    await expect(readRegularShareCardFile(join(tmp, "tweet-gex-2026-11-16.html"))).resolves.toBeNull();
  });
});

describe("readShareCard", () => {
  const planted = join(SHARE_CARDS_DIR, "tweet-vcg-1999-01-01.html");
  const createdDir = !existsSync(SHARE_CARDS_DIR);

  beforeAll(() => {
    mkdirSync(SHARE_CARDS_DIR, { recursive: true });
    symlinkSync(outside, planted);
  });

  afterAll(() => {
    rmSync(planted, { force: true });
    if (createdDir) rmSync(SHARE_CARDS_DIR, { recursive: true, force: true });
  });

  it("returns null for a link planted under an allowed card name", async () => {
    await expect(readShareCard(planted, "vcg")).resolves.toBeNull();
  });
});
