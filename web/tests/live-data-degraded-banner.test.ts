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

    // The error falls back across the three live-data sources. The expression
    // may be wrapped (e.g. `isDemoMode ? null : (...)`), so assert the fallback
    // chain itself rather than the exact `const` prefix to avoid brittle breaks.
    expect(source).toContain("portfolioError ?? ordersError ?? priceError");
    expect(source).toContain('data-testid="live-data-degraded"');
    expect(source).toContain("Live data degraded");
    expect(source).toContain("<span>{liveDataError}</span>");
  });
});
