import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

describe("demo workspace sync chrome", () => {
  it("describes the static sample without offering an IB producer action", () => {
    const source = readFileSync(
      resolve(__dirname, "..", "components", "WorkspaceShell.tsx"),
      "utf8",
    );

    expect(source).toContain('isDemoMode ? "Sample snapshot"');
    expect(source).toContain("{!isDemoMode ? (");
    expect(source).toContain('title={`Sync ${syncTarget} from IB Gateway`}');
  });
});
