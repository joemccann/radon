/**
 * @vitest-environment jsdom
 *
 * R-229 — a missing risk-free rate must not read as 0%.
 *
 * `getSnapshot()` was `cachedRate ?? 0`, so "FRED says the effective Fed Funds
 * rate is 0%" and "FRED never answered" were the same value to every consumer.
 * The route answers any failure with `{rate: 0, source: "fallback", stale: true}`
 * at HTTP 200; the hook correctly refuses to cache that but then left
 * `cachedRate === null` with only a `useEffect(..., [])` that fires once per
 * consumer mount, and `loadRiskFreeRate` short-circuits on `inFlight` — no
 * timer, no retry, no backoff, no error state. On a long-lived WorkspaceShell
 * session one transient FRED miss at page load pinned `r = 0` for the rest of
 * the session, and `PositionTable` feeds that straight into
 * `computePositionImpliedValue` for the Implied columns the operator compares
 * against Last Price. Every function in the chain also defaults
 * `riskFreeRate = 0`, so the fallback was indistinguishable from a real
 * observation at every layer.
 */

import { act } from "react";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  resetRiskFreeRateCacheForTests,
  useRiskFreeRate,
  useRiskFreeRateState,
} from "../lib/useRiskFreeRate";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const FALLBACK = { rate: 0, source: "fallback", stale: true };
const REAL = { rate: 0.0433, source: "FRED:DFF", stale: false };

function StateProbe() {
  const { rate, resolved } = useRiskFreeRateState();
  return (
    <output data-testid="probe">{resolved ? String(rate) : "unresolved"}</output>
  );
}

beforeEach(() => {
  resetRiskFreeRateCacheForTests();
  vi.useFakeTimers({ shouldAdvanceTime: true });
});

afterEach(() => {
  cleanup();
  resetRiskFreeRateCacheForTests();
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("useRiskFreeRateState", () => {
  it("reports unresolved rather than 0 when the route serves its fallback", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => FALLBACK }));
    render(<StateProbe />);
    await waitFor(() => expect(screen.getByTestId("probe").textContent).toBe("unresolved"));
  });

  it("retries on a timer instead of waiting for another consumer to mount", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({ ok: true, json: async () => FALLBACK })
      .mockResolvedValue({ ok: true, json: async () => REAL });
    vi.stubGlobal("fetch", fetchMock);

    render(<StateProbe />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));

    // Nothing else mounts, nothing re-renders. Only an internal timer can
    // recover this session.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(70_000);
    });

    await waitFor(() => expect(screen.getByTestId("probe").textContent).toBe("0.0433"));
  });

  it("backs off rather than hammering a persistently failing route", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => FALLBACK });
    vi.stubGlobal("fetch", fetchMock);

    render(<StateProbe />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));

    await act(async () => {
      await vi.advanceTimersByTimeAsync(10 * 60_000);
    });

    const calls = fetchMock.mock.calls.length;
    expect(calls).toBeGreaterThan(1);
    expect(calls).toBeLessThan(12);
  });

  it("stops retrying once a real observation lands", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => REAL });
    vi.stubGlobal("fetch", fetchMock);

    render(<StateProbe />);
    await waitFor(() => expect(screen.getByTestId("probe").textContent).toBe("0.0433"));

    await act(async () => {
      await vi.advanceTimersByTimeAsync(10 * 60_000);
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

describe("useRiskFreeRate — the legacy number contract", () => {
  it("still hands consumers a usable number", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => REAL }));
    function NumberProbe() {
      return <output data-testid="n">{useRiskFreeRate()}</output>;
    }
    render(<NumberProbe />);
    await waitFor(() => expect(screen.getByTestId("n").textContent).toBe("0.0433"));
  });
});

describe("PositionTable Implied columns", () => {
  it("does not price off r = 0 while the rate is unresolved", async () => {
    const { readFileSync } = await import("fs");
    const { join } = await import("path");
    const text = readFileSync(
      join(__dirname, "..", "components", "PositionTable.tsx"),
      "utf-8",
    );
    const body = text
      .split("\n")
      .filter((line) => !line.trim().startsWith("//") && !line.trim().startsWith("*"))
      .join("\n");
    expect(body).toContain("useRiskFreeRateState");
    expect(body).not.toContain("riskFreeRate = 0");
    // Every computePositionImpliedValue / computeLegImpliedValue call site is
    // behind a null check on the rate.
    const guards = body.match(/riskFreeRate == null/g) ?? [];
    expect(guards.length).toBeGreaterThanOrEqual(5);
  });
});
