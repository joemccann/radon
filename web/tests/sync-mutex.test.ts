import { describe, it, expect } from "vitest";
import { createSyncMutex } from "../lib/syncMutex";

// =============================================================================
// Sync mutex -- coalesces concurrent calls into a single execution
// =============================================================================

describe("createSyncMutex", () => {
  it("executes the function on first call", async () => {
    let callCount = 0;
    const mutex = createSyncMutex(async () => {
      callCount++;
      return { ok: true, stderr: "" };
    });

    const result = await mutex();
    expect(callCount).toBe(1);
    expect(result.ok).toBe(true);
  });

  it("coalesces concurrent calls -- runs fn only once", async () => {
    let callCount = 0;
    const mutex = createSyncMutex(async () => {
      callCount++;
      await new Promise((r) => setTimeout(r, 50));
      return { ok: true, stderr: "" };
    });

    const [r1, r2, r3] = await Promise.all([mutex(), mutex(), mutex()]);
    expect(callCount).toBe(1);
    expect(r1.ok).toBe(true);
    expect(r2.ok).toBe(true);
    expect(r3.ok).toBe(true);
  });

  it("allows a new call after the previous one finishes", async () => {
    let callCount = 0;
    const mutex = createSyncMutex(async () => {
      callCount++;
      await new Promise((r) => setTimeout(r, 10));
      return { ok: true, stderr: `call-${callCount}` };
    });

    const r1 = await mutex();
    expect(callCount).toBe(1);
    expect(r1.stderr).toBe("call-1");

    const r2 = await mutex();
    expect(callCount).toBe(2);
    expect(r2.stderr).toBe("call-2");
  });

  it("propagates errors to all waiters and resets", async () => {
    let callCount = 0;
    const mutex = createSyncMutex(async () => {
      callCount++;
      await new Promise((r) => setTimeout(r, 20));
      throw new Error("sync failed");
    });

    const results = await Promise.allSettled([mutex(), mutex()]);
    expect(callCount).toBe(1);
    expect(results[0].status).toBe("rejected");
    expect(results[1].status).toBe("rejected");
    expect((results[0] as PromiseRejectedResult).reason.message).toBe("sync failed");
    expect((results[1] as PromiseRejectedResult).reason.message).toBe("sync failed");

    // After error, mutex should be reset -- new call should work
    callCount = 0;
    const mutex2 = createSyncMutex(async () => {
      callCount++;
      return { ok: true, stderr: "" };
    });
    const r = await mutex2();
    expect(r.ok).toBe(true);
  });

  it("second wave of calls after first wave completes runs fn again", async () => {
    let callCount = 0;
    const mutex = createSyncMutex(async () => {
      callCount++;
      await new Promise((r) => setTimeout(r, 20));
      return { ok: true, stderr: `wave-${callCount}` };
    });

    // Wave 1
    const [w1a, w1b] = await Promise.all([mutex(), mutex()]);
    expect(callCount).toBe(1);
    expect(w1a.stderr).toBe("wave-1");
    expect(w1b.stderr).toBe("wave-1");

    // Wave 2
    const [w2a, w2b] = await Promise.all([mutex(), mutex()]);
    expect(callCount).toBe(2);
    expect(w2a.stderr).toBe("wave-2");
    expect(w2b.stderr).toBe("wave-2");
  });
});

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
