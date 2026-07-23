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

    // 2026-07-22: the fallback chain (portfolioError ?? ordersError ??
    // priceError) moved into deriveLiveDataError in lib/offline/offlineStatus
    // so browser-offline can suppress the raw banner. Pin that WorkspaceShell
    // routes through the helper AND that the helper still owns the chain.
    expect(source).toContain("deriveLiveDataError({");
    expect(source).toContain("portfolioError,");
    expect(source).toContain("ordersError,");
    expect(source).toContain("priceError,");
    expect(source).toContain('data-testid="live-data-degraded"');
    expect(source).toContain("Live data degraded");
    expect(source).toContain("<span>{liveDataError}</span>");

    const helperSource = readFileSync(
      resolve(projectRoot, "lib", "offline", "offlineStatus.ts"),
      "utf8",
    );
    expect(helperSource).toContain("portfolioError ?? input.ordersError ?? input.priceError");
  });
});
