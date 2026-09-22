import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

// Security floors from the 2026-09-14 bun audit (PR #432 follow-up). Each
// entry pins the RESOLVED version in the lockfile, not the package.json range,
// so a stale transitive (jsdom -> undici, @libsql/isomorphic-ws -> ws) fails
// here even when the direct range already looks fine.
const ROOT = resolve(__dirname, "../..");

const FLOORS: Array<{ lock: string; key: string; name: string; floor: string }> = [
  { lock: "bun.lock", key: "sharp", name: "sharp", floor: "0.35.0" },
  { lock: "bun.lock", key: "axios", name: "axios", floor: "1.16.0" },
  { lock: "bun.lock", key: "form-data", name: "form-data", floor: "4.0.6" },
  { lock: "bun.lock", key: "undici", name: "undici", floor: "7.29.0" },
  { lock: "bun.lock", key: "ws", name: "ws", floor: "8.21.0" },
  { lock: "web/bun.lock", key: "undici", name: "undici", floor: "8.9.0" },
  { lock: "web/bun.lock", key: "ws", name: "ws", floor: "8.21.0" },
];

function parse(v: string): number[] {
  return v.split(".").map((n) => Number.parseInt(n, 10));
}

function atLeast(actual: string, floor: string): boolean {
  const a = parse(actual);
  const f = parse(floor);
  for (let i = 0; i < 3; i += 1) {
    if (a[i] !== f[i]) return a[i] > f[i];
  }
  return true;
}

// Every lockfile entry whose package name matches, including nested aliases
// such as `"@libsql/isomorphic-ws/ws": ["ws@8.19.0", ...]`.
function resolvedVersions(lock: string, name: string): string[] {
  const escaped = name.replace(/[/@.]/g, (c) => `\\${c}`);
  const re = new RegExp(`^\\s*"(?:[^"]*/)?${escaped}": \\["${escaped}@(\\d+\\.\\d+\\.\\d+)"`, "gm");
  return [...lock.matchAll(re)].map((m) => m[1]);
}

describe("dependency security floors", () => {
  for (const { lock, name, floor } of FLOORS) {
    it(`${lock}: every resolved ${name} is >= ${floor}`, async () => {
      const text = await readFile(resolve(ROOT, lock), "utf8");
      const versions = resolvedVersions(text, name);
      expect(versions.length, `${name} missing from ${lock}`).toBeGreaterThan(0);
      for (const v of versions) {
        expect(atLeast(v, floor), `${name}@${v} in ${lock} is below ${floor}`).toBe(true);
      }
    });
  }
});
