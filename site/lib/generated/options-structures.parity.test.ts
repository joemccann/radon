import { describe, expect, it } from "vitest";
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";

import vendored from "./options-structures.json";

// The site bundle cannot read ../docs at runtime on Vercel, so the catalog is
// vendored here. This test fails the build if the copy drifts from the source.
describe("vendored options-structures.json", () => {
  it("matches docs/options-structures.json", () => {
    const candidates = [
      path.join(process.cwd(), "..", "docs", "options-structures.json"),
      path.join(process.cwd(), "docs", "options-structures.json"),
    ];
    const source = candidates.find((candidate) => existsSync(candidate));
    expect(source).toBeDefined();
    expect(vendored).toEqual(JSON.parse(readFileSync(source!, "utf8")));
  });
});
