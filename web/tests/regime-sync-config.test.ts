import { describe, expect, it } from "vitest";
import { readFileSync } from "fs";
import { join } from "path";
import { fileURLToPath } from "url";

const TEST_DIR = fileURLToPath(new URL(".", import.meta.url));
const HOOK_PATH = join(TEST_DIR, "../lib/useRegime.ts");
const source = readFileSync(HOOK_PATH, "utf-8");

describe("useRegime config", () => {
  it("polls /api/regime every minute with GET refreshes", () => {
    expect(source).toContain('endpoint: "/api/regime"');
    expect(source).toContain("interval: 60_000");
    expect(source).toContain("hasPost: false");
  });
});
