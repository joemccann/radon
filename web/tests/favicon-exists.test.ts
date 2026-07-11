/**
 * Regression: production logs showed `GET /favicon.ico 404` because the
 * Browsers request the literal `/favicon.ico` regardless of metadata. Keep it
 * in `public/` so Next serves it as a static asset. The App Router metadata
 * route for `app/favicon.ico` produced an invalid `favicon.ico/route` artifact
 * in Vercel's compile-mode onBuildComplete adapter on 2026-07-11.
 *
 * This test pins the asset: it must exist on disk, be non-empty, and
 * begin with the ICO magic bytes (`00 00 01 00`) so a browser will
 * accept it.
 */
import { describe, it, expect } from "vitest";
import { readFileSync, statSync } from "node:fs";
import { resolve } from "node:path";

const FAVICON_PATH = resolve(__dirname, "../public/favicon.ico");

describe("static favicon", () => {
  it("ships public/favicon.ico so browsers don't 404 on /favicon.ico", () => {
    const stats = statSync(FAVICON_PATH);
    expect(stats.isFile()).toBe(true);
    expect(stats.size).toBeGreaterThan(0);
  });

  it("is a valid ICO file (correct magic bytes)", () => {
    const buf = readFileSync(FAVICON_PATH);
    // ICO header: reserved (2 bytes = 0x0000), type (2 bytes = 0x0001 for ICO).
    expect(buf[0]).toBe(0x00);
    expect(buf[1]).toBe(0x00);
    expect(buf[2]).toBe(0x01);
    expect(buf[3]).toBe(0x00);
  });
});
