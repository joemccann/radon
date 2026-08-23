import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");

describe("flow tabs do not auto-POST; SCAN and the VPS timer own UW walks", () => {
  it.each(["lib/useDiscover.ts", "lib/useScanner.ts", "lib/useFlowAnalysis.ts"])(
    "%s sets interval: 0",
    (rel) => {
      const source = readFileSync(join(root, rel), "utf8");
      expect(source).toContain("interval: 0");
    },
  );
});
