import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

describe("CTA route output tracing", () => {
  it("excludes the host-mounted data tree only from the CTA route package", async () => {
    const { default: config } = await import("../next.config.mjs");

    expect(config.outputFileTracingExcludes).toEqual({
      "/api/menthorq/cta": ["../data/**/*"],
    });
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
});
