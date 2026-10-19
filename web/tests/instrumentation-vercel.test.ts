/**
 * Vercel runs the app as serverless functions that are frozen between
 * invocations. Background timers started there fire in bursts on thaw: the
 * loop-lag monitor reported "event loop blocked ~299618ms" (the freeze, not a
 * blocked loop) and the Turso keep-alive ping reset the pool mid-request.
 * Both belong to the long-lived Node server only.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const startDbKeepAlive = vi.fn();
const startLoopLagMonitor = vi.fn();
const installBoundedShutdown = vi.fn();

vi.mock("@/lib/setup/setupComplete", () => ({ syncSetupCompleteFromMarker: vi.fn(async () => {}) }));
vi.mock("@/lib/dbKeepAlive", () => ({ startDbKeepAlive }));
vi.mock("@/lib/loopLagMonitor", () => ({ startLoopLagMonitor }));
vi.mock("@/lib/boundedShutdown", () => ({ installBoundedShutdown }));

import { register } from "../instrumentation";

beforeEach(() => {
  vi.clearAllMocks();
  vi.stubEnv("NEXT_RUNTIME", "nodejs");
  vi.stubEnv("NODE_ENV", "production");
});

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("instrumentation register", () => {
  it("skips the keep-alive and loop-lag monitor on Vercel", async () => {
    vi.stubEnv("VERCEL", "1");
    await register();
    expect(startDbKeepAlive).not.toHaveBeenCalled();
    expect(startLoopLagMonitor).not.toHaveBeenCalled();
  });

  it("starts both on the long-lived production Node server", async () => {
    vi.stubEnv("VERCEL", "");
    await register();
    expect(startDbKeepAlive).toHaveBeenCalledTimes(1);
    expect(startLoopLagMonitor).toHaveBeenCalledTimes(1);
    expect(installBoundedShutdown).toHaveBeenCalledTimes(1);
  });
});
