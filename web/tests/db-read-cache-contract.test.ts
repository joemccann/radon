import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const REPO_ROOT = join(__dirname, "..");

// Hot polled routes MUST coalesce their Turso reads through the in-process
// TTL cache (lib/dbCache.ts). Each client tab polls these every 5s-5min;
// without cachedRead every poll from every tab pays the ~12ms (warm) to
// ~80ms (cold) Turso round trip for identical data. The cache key is pinned
// here so a refactor can't silently fork two entries for the same read.
//
// portfolio / service-health / gex / blotter established the pattern
// (commit 7ef0b850); this contract keeps the rest of the family in line.
const CACHED_READ_ROUTES: { path: string; key: string }[] = [
  { path: "app/api/vcg/route.ts", key: "vcg:snapshot" },
  { path: "app/api/regime/route.ts", key: "regime:cri" },
  { path: "app/api/gamma-rotation/route.ts", key: "gamma-rotation:snapshot" },
  { path: "app/api/scanner/route.ts", key: "scanner:snapshot" },
  { path: "app/api/scanner/theta/route.ts", key: "scanner:theta" },
  { path: "app/api/scanner/strength/route.ts", key: "scanner:strength" },
  { path: "app/api/discover/route.ts", key: "discover:snapshot" },
  { path: "app/api/flow-analysis/route.ts", key: "flow-analysis:snapshot" },
  { path: "app/api/newsfeed/posts/route.ts", key: "newsfeed:posts" },
  { path: "app/api/admin/host-metrics/route.ts", key: "admin:host-metrics" },
  { path: "app/api/admin/reliability/route.ts", key: "admin:reliability" },
  { path: "app/api/admin/slo/route.ts", key: "admin:slo" },
];

function stripComments(src: string): string {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/(^|[^:])\/\/[^\n]*/g, "$1");
}

describe("DB read cache — hot polled routes must read through cachedRead", () => {
  it.each(CACHED_READ_ROUTES)(
    "$path coalesces its DB read under cache key $key",
    async ({ path, key }) => {
      const stripped = stripComments(
        await readFile(join(REPO_ROOT, path), "utf8"),
      );
      expect(stripped).toMatch(/\bcachedRead\s*(<[^>]*>)?\s*\(/);
      expect(stripped).toContain(`"${key}"`);
    },
  );
});

// Routes whose POST triggers a synchronous rescan must drop the cached GET
// entry on success, so the next poll serves the fresh snapshot instead of
// waiting out the TTL.
const INVALIDATING_POST_ROUTES: { path: string; key: string }[] = [
  { path: "app/api/scanner/route.ts", key: "scanner:snapshot" },
  { path: "app/api/discover/route.ts", key: "discover:snapshot" },
  { path: "app/api/flow-analysis/route.ts", key: "flow-analysis:snapshot" },
  { path: "app/api/regime/route.ts", key: "regime:cri" },
  { path: "app/api/gamma-rotation/route.ts", key: "gamma-rotation:snapshot" },
];

describe("DB read cache — scan POSTs must invalidate the GET cache entry", () => {
  it.each(INVALIDATING_POST_ROUTES)(
    "$path invalidates $key after a scan",
    async ({ path }) => {
      const stripped = stripComments(
        await readFile(join(REPO_ROOT, path), "utf8"),
      );
      expect(stripped).toMatch(/\binvalidateCache\s*\(/);
    },
  );
});

// The embedded replica is retired (direct-to-cloud, DUR-07): syncDb() is a
// no-op that only survives as replica-era vestige. The newsfeed route was
// the last caller — it must stay gone.
describe("newsfeed/posts — no replica-era syncDb call", () => {
  it("does not import or call syncDb", async () => {
    const src = await readFile(
      join(REPO_ROOT, "app/api/newsfeed/posts/route.ts"),
      "utf8",
    );
    expect(src).not.toMatch(/\bsyncDb\b/);
  });
});
