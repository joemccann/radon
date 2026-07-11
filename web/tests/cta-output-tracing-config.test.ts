import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

describe("host-data output tracing", () => {
  it("excludes the host-mounted data tree from disk-fallback API routes", async () => {
    const { default: config } = await import("../next.config.mjs");

    expect(config.outputFileTracingExcludes["/api/menthorq/cta"]).toEqual([
      "../data/**/*",
    ]);
    expect(config.outputFileTracingExcludes["/api/internals"]).toEqual([
      "../data/**/*",
    ]);
    expect(config.outputFileTracingExcludes["/api/regime"]).toEqual([
      "../data/**/*",
    ]);
    expect(
      Object.values(config.outputFileTracingExcludes).every(
        (patterns) =>
          Array.isArray(patterns)
          && patterns.length === 1
          && patterns[0] === "../data/**/*",
      ),
    ).toBe(true);
  });

  it("keeps the host runtime root opaque to Turbopack tracing", () => {
    const route = readFileSync(
      resolve(__dirname, "../app/api/menthorq/cta/route.ts"),
      "utf8",
    );

    expect(route).toContain(
      'join(/* turbopackIgnore: true */ process.cwd(), "..")',
    );
    expect(route).toContain(
      'readFile(/* turbopackIgnore: true */ path, "utf-8")',
    );
    expect(route).toContain(
      "readdir(/* turbopackIgnore: true */ CACHE_DIR)",
    );
    expect(route).toMatch(
      /readFile\(\s*\/\* turbopackIgnore: true \*\/ join\(CACHE_DIR, latestFile\)/,
    );
    expect(route).toMatch(
      /stat\(\s*\/\* turbopackIgnore: true \*\/ join\(CACHE_DIR, latestFile\)/,
    );
  });

  it("keeps internals and regime project roots opaque to Turbopack tracing", () => {
    for (const relativePath of [
      "../app/api/internals/route.ts",
      "../app/api/regime/route.ts",
    ]) {
      const route = readFileSync(resolve(__dirname, relativePath), "utf8");
      expect(route).toContain(
        'join(/* turbopackIgnore: true */ process.cwd(), "..")',
      );
    }
  });
});
