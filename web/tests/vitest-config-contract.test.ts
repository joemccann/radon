/**
 * Config contract for the root vitest config (TEST_AUDIT T-161).
 *
 * A suite-wide `retry` converts every intermittent money-path failure into a
 * green deploy gate, and the repo's own debugging rule ("re-run the suspect
 * test file in isolation before concluding your change caused it") depends on
 * the first failure being reported. Two jsdom files that timed out under
 * shard + coverage load are the whole reason a retry was ever added; they get
 * a raised per-file `testTimeout` instead, which is honest about what is slow
 * and leaves the other ~7.5k tests fail-fast.
 */
import { describe, expect, it } from "vitest";
import { readFileSync } from "fs";
import { join } from "path";
import { fileURLToPath } from "url";

const REPO_ROOT = join(fileURLToPath(new URL(".", import.meta.url)), "..", "..");
const configSource = readFileSync(join(REPO_ROOT, "vitest.config.ts"), "utf-8");

// The two jsdom files named in the retry comment this contract replaces.
const SLOW_JSDOM_FILES = [
  "web/tests/dashboard-newsfeed-pagination.test.tsx",
  "web/tests/theta-harvester-scanner.test.tsx",
];

describe("vitest global config", () => {
  it("never retries: a suite-wide retry hides intermittent money-path failures", () => {
    const retry = /^\s*retry:\s*(.+?),?\s*$/m.exec(configSource);
    expect(retry?.[1] ?? "0").toBe("0");
  });

  it("keeps the slow jsdom files fail-fast with a raised per-file timeout", () => {
    for (const file of SLOW_JSDOM_FILES) {
      const source = readFileSync(join(REPO_ROOT, file), "utf-8");
      const setConfig = /vi\.setConfig\(\{[^}]*testTimeout:\s*([0-9_]+)/.exec(source);
      expect(setConfig, `${file} must raise testTimeout via vi.setConfig`).not.toBeNull();
      expect(Number(setConfig![1].replace(/_/g, ""))).toBeGreaterThan(5000);
      expect(source, `${file} must not set a per-file retry`).not.toMatch(/retry:\s*[1-9]/);
    }
  });
});
