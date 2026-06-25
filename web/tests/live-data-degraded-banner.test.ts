import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const projectRoot = resolve(__dirname, "..");

describe("WorkspaceShell live-data degraded banner", () => {
  it("surfaces portfolio, order, and price-stream failures through the shared shell", () => {
    const source = readFileSync(
      resolve(projectRoot, "components", "WorkspaceShell.tsx"),
      "utf8",
    );

    expect(source).toContain("const liveDataError = portfolioError ?? ordersError ?? priceError");
    expect(source).toContain('data-testid="live-data-degraded"');
    expect(source).toContain("Live data degraded");
    expect(source).toContain("<span>{liveDataError}</span>");
  });
});
