import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const ROOT = fileURLToPath(new URL("../../", import.meta.url));
const indexedOrder = "ORDER BY date DESC, taken_at DESC LIMIT 1";

describe("latest CRI queries use the existing composite index", () => {
  for (const relative of [
    "web/app/api/regime/route.ts",
    "web/app/api/internals/route.ts",
    "scripts/api/demo_scan.py",
  ]) {
    it(relative, () => {
      const source = readFileSync(join(ROOT, relative), "utf8");
      expect(source).toContain(indexedOrder);
      expect(source).not.toContain(
        "FROM cri_snapshots ORDER BY taken_at DESC LIMIT 1",
      );
    });
  }
});
