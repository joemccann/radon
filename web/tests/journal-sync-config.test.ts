import { describe, expect, it } from "vitest";
import { readFileSync } from "fs";
import { join } from "path";
import { fileURLToPath } from "url";

const TEST_DIR = fileURLToPath(new URL(".", import.meta.url));
const HOOK_PATH = join(TEST_DIR, "../lib/useJournal.ts");
const source = readFileSync(HOOK_PATH, "utf-8");

describe("useJournal config", () => {
  it("uses POST sync on mount for /api/journal with polling disabled", () => {
    expect(source).toContain('endpoint: "/api/journal"');
    expect(source).toContain("hasPost: true");
    expect(source).toContain("interval: 0");
  });
});
