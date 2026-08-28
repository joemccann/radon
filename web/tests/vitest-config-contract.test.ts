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
 *
 * T-233 extends the same idea to the two ambient-environment inputs the suite
 * silently reads: the machine's timezone and the machine's locale. Both were
 * PROPERTIES that happened to hold on a US developer laptop and had to be
 * re-audited on every delta; pinning them in `test.env` makes them GUARANTEES.
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

/** The `test.env` block, parsed out of the config source. */
const envBlock = /^\s*env:\s*\{([\s\S]*?)\}\s*,/m.exec(configSource)?.[1] ?? "";
const pinned = (key: string) =>
  new RegExp(`\\b${key}:\\s*"([^"]+)"`).exec(envBlock)?.[1];

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

  // T-233. The config pins NODE_ENV; it must pin the two other ambient inputs
  // the suite reads without ever naming them.
  it("pins NODE_ENV, TZ and the locale so no ambient shell value leaks in", () => {
    expect(pinned("NODE_ENV")).toBe("test");
    // Every market-hours / session / ET-day-cut helper in web/lib resolves "the
    // calendar day" against America/New_York. A suite run in any other zone
    // asserts a different day for the same instant — web/tests/format-trade-date
    // .test.ts:28 ("renders an ISO timestamp using its local-tz calendar day")
    // reds under TZ=Pacific/Kiritimati purely because of the developer's shell.
    expect(pinned("TZ")).toBe("America/New_York");
    // Product code formats money with an EXPLICIT toLocaleString("en-US") (30+
    // call sites under web/lib); tests routinely compare against a BARE
    // toLocaleString(), whose locale is env-driven. Under LC_ALL=de_DE.UTF-8 the
    // expectation becomes "1.030" while the component still renders "1,030".
    // ICU reads LC_ALL first, then LANG, so both are pinned.
    expect(pinned("LC_ALL")).toBe("en_US.UTF-8");
    expect(pinned("LANG")).toBe("en_US.UTF-8");
  });

  // The static assertions above guard the source text; these guard the effect,
  // so the contract also reds if vitest ever stops applying `test.env`.
  it("actually runs under ET and en-US, whatever the shell exported", () => {
    expect(Intl.DateTimeFormat().resolvedOptions().timeZone).toBe("America/New_York");
    expect(new Date("2026-05-08T17:30:00Z").getDate()).toBe(8);
    expect(Intl.NumberFormat().resolvedOptions().locale).toBe("en-US");
    // The exact shape the bare-vs-explicit toLocaleString mismatch takes.
    expect((12_120).toLocaleString()).toBe((12_120).toLocaleString("en-US"));
  });
});
