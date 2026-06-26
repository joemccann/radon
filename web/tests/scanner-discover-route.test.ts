import { readFileSync } from "fs";
import { join } from "path";
import { describe, expect, it } from "vitest";

const TEST_DIR = __dirname;

describe("/discover route consolidation", () => {
  it("redirects the legacy route to scanner discover mode", () => {
    const source = readFileSync(join(TEST_DIR, "../app/discover/page.tsx"), "utf-8");

    expect(source).toMatch(/import\s+\{\s*redirect\s*\}\s+from\s+"next\/navigation"/);
    expect(source).toContain('redirect("/scanner?mode=discover")');
    expect(source).not.toContain('WorkspaceShell section="discover"');
  });

  it("keeps the scanner route mounted on the workspace shell", () => {
    const source = readFileSync(join(TEST_DIR, "../app/scanner/page.tsx"), "utf-8");

    expect(source).toContain('WorkspaceShell section="scanner"');
  });
});
