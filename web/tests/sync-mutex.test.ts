import { describe, it, expect } from "vitest";

// =============================================================================
// Route-level integration: orders route uses the mutex
// =============================================================================

describe("Orders route uses radonFetch", () => {
  it("orders route uses radonFetch for sync", async () => {
    const { readFile } = await import("node:fs/promises");
    const { resolve } = await import("node:path");
    const { fileURLToPath } = await import("node:url");
    const __dirname = resolve(fileURLToPath(import.meta.url), "..");

    const content = await readFile(resolve(__dirname, "../app/api/orders/route.ts"), "utf8");
    expect(content.includes("radonFetch")).toBeTruthy();
    expect(content.includes("/orders/refresh")).toBeTruthy();
  });

  it("cancel and modify routes use radonFetch", async () => {
    const { readFile } = await import("node:fs/promises");
    const { resolve } = await import("node:path");
    const { fileURLToPath } = await import("node:url");
    const __dirname = resolve(fileURLToPath(import.meta.url), "..");

    const cancelContent = await readFile(resolve(__dirname, "../app/api/orders/cancel/route.ts"), "utf8");
    expect(cancelContent.includes("radonFetch")).toBeTruthy();

    const modifyContent = await readFile(resolve(__dirname, "../app/api/orders/modify/route.ts"), "utf8");
    expect(modifyContent.includes("radonFetch")).toBeTruthy();
  });
});
