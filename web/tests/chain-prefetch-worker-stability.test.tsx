/**
 * @vitest-environment jsdom
 *
 * R-662: the prefetch effect was keyed on the raw `expirations` array, so a
 * same-content re-render (new array identity every parent render) aborted and
 * restarted the staggered worker, resetting the 3s initial delay forever and
 * starving the background cache.
 */

import { renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useChainPrefetch } from "../lib/useChainPrefetch";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("useChainPrefetch worker stability", () => {
  it("does not abort/restart the worker on a same-content expirations re-render", () => {
    vi.stubGlobal("fetch", vi.fn(() => new Promise(() => {})));
    const abortSpy = vi.spyOn(AbortController.prototype, "abort");

    const { rerender } = renderHook(
      ({ exps }: { exps: string[] }) =>
        useChainPrefetch("SPY", exps, "2026-09-18", true),
      { initialProps: { exps: ["2026-09-18", "2026-10-16", "2026-11-20"] } },
    );

    expect(abortSpy).not.toHaveBeenCalled();

    // Fresh array identity, identical content — worker must stay put.
    rerender({ exps: ["2026-09-18", "2026-10-16", "2026-11-20"] });
    expect(abortSpy).not.toHaveBeenCalled();

    // Different content — worker legitimately restarts.
    rerender({ exps: ["2026-09-18", "2026-10-16", "2026-12-18"] });
    expect(abortSpy).toHaveBeenCalled();
  });
});
