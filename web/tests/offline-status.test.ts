/**
 * Pure offline-status decision core — reducer, header meta parsing, label
 * derivations. Fixture pattern per staleDataMachine/margin-warning: fixed
 * literal NOW, constant-pinning describe, exhaustive branch matrix.
 */
import { describe, it, expect } from "vitest";
import {
  OFFLINE_ENTER_DELAY_MS,
  FAILURES_TO_TRIP,
  FAILURE_WINDOW_MS,
  INITIAL_OFFLINE_STATE,
  offlineReducer,
  readOfflineMeta,
  formatAsOf,
  deriveLiveDataError,
  resolveConnectivityLabel,
  resolveMobileConnectivityChip,
  type OfflineState,
  type OfflineSignal,
} from "../lib/offline/offlineStatus";

const NOW = 1_700_000_000_000;

function run(signals: Array<{ signal: OfflineSignal; at: number }>): OfflineState {
  let state = INITIAL_OFFLINE_STATE;
  for (const { signal, at } of signals) {
    state = offlineReducer(state, signal, at);
  }
  return state;
}

function headersOf(record: Record<string, string>): { get(name: string): string | null } {
  return { get: (name: string) => record[name] ?? null };
}

describe("pinned constants", () => {
  it("pins the documented thresholds", () => {
    expect(OFFLINE_ENTER_DELAY_MS).toBe(2000);
    expect(FAILURES_TO_TRIP).toBe(2);
    expect(FAILURE_WINDOW_MS).toBe(45_000);
  });
});

describe("readOfflineMeta", () => {
  it("parses X-Radon-Offline: 1 with an ISO cached-at stamp", () => {
    const iso = new Date(NOW).toISOString();
    const meta = readOfflineMeta(headersOf({ "X-Radon-Offline": "1", "X-Radon-Cached-At": iso }));
    expect(meta).toEqual({ servedOffline: true, cachedAt: NOW });
  });

  it("parses a numeric epoch-ms cached-at stamp", () => {
    const meta = readOfflineMeta(
      headersOf({ "X-Radon-Offline": "1", "X-Radon-Cached-At": String(NOW) }),
    );
    expect(meta).toEqual({ servedOffline: true, cachedAt: NOW });
  });

  it("absent headers → not offline-served", () => {
    expect(readOfflineMeta(headersOf({}))).toEqual({ servedOffline: false, cachedAt: null });
  });

  it("garbage cached-at → null", () => {
    const meta = readOfflineMeta(
      headersOf({ "X-Radon-Offline": "1", "X-Radon-Cached-At": "garbage" }),
    );
    expect(meta).toEqual({ servedOffline: true, cachedAt: null });
  });

  it('X-Radon-Offline: "0" → not offline-served', () => {
    expect(readOfflineMeta(headersOf({ "X-Radon-Offline": "0" }))).toEqual({
      servedOffline: false,
      cachedAt: null,
    });
  });

  it("missing/malformed headers object → safe default", () => {
    expect(readOfflineMeta(null)).toEqual({ servedOffline: false, cachedAt: null });
    expect(readOfflineMeta(undefined)).toEqual({ servedOffline: false, cachedAt: null });
  });
});

describe("offlineReducer — navigator-offline debounce", () => {
  it("navigator-offline alone does not go offline", () => {
    const state = run([{ signal: { type: "navigator-offline" }, at: NOW }]);
    expect(state.offline).toBe(false);
  });

  it("goes offline when the enter delay elapses", () => {
    const state = run([
      { signal: { type: "navigator-offline" }, at: NOW },
      { signal: { type: "enter-delay-elapsed" }, at: NOW + OFFLINE_ENTER_DELAY_MS },
    ]);
    expect(state.offline).toBe(true);
  });

  it("a premature delay-elapsed does not trip (blip guard)", () => {
    const state = run([
      { signal: { type: "navigator-offline" }, at: NOW },
      { signal: { type: "enter-delay-elapsed" }, at: NOW + 1000 },
    ]);
    expect(state.offline).toBe(false);
  });

  it("navigator-online cancels a pending enter", () => {
    const state = run([
      { signal: { type: "navigator-offline" }, at: NOW },
      { signal: { type: "navigator-online" }, at: NOW + 1000 },
      { signal: { type: "enter-delay-elapsed" }, at: NOW + OFFLINE_ENTER_DELAY_MS },
    ]);
    expect(state.offline).toBe(false);
  });

  it("delay-elapsed with no pending offline is a no-op", () => {
    const state = run([{ signal: { type: "enter-delay-elapsed" }, at: NOW }]);
    expect(state.offline).toBe(false);
  });
});

describe("offlineReducer — fetch failures", () => {
  it("1 failure does not trip", () => {
    const state = run([{ signal: { type: "fetch-failure" }, at: NOW }]);
    expect(state.offline).toBe(false);
  });

  it("2 failures within the window trip offline", () => {
    const state = run([
      { signal: { type: "fetch-failure" }, at: NOW },
      { signal: { type: "fetch-failure" }, at: NOW + 10_000 },
    ]);
    expect(state.offline).toBe(true);
  });

  it("2 failures 46s apart do NOT trip", () => {
    const state = run([
      { signal: { type: "fetch-failure" }, at: NOW },
      { signal: { type: "fetch-failure" }, at: NOW + 46_000 },
    ]);
    expect(state.offline).toBe(false);
  });
});

describe("offlineReducer — offline-served", () => {
  // Same 2s debounce as navigator-offline: one blipped poll served from SW
  // cache amid healthy traffic must not flash the banner; a real outage
  // enters after the delay with cachedAt preserved.
  it("arms the enter delay (no instant trip) and enters after it elapses with cachedAt", () => {
    let state = run([
      { signal: { type: "offline-served", cachedAt: NOW - 60_000 }, at: NOW },
    ]);
    expect(state.offline).toBe(false);
    expect(state.pendingEnterAt).toBe(NOW);
    state = offlineReducer(state, { type: "enter-delay-elapsed" }, NOW + 2000);
    expect(state.offline).toBe(true);
    expect(state.cachedAt).toBe(NOW - 60_000);
  });

  it("cachedAt is max-wins across responses", () => {
    const state = run([
      { signal: { type: "offline-served", cachedAt: NOW - 60_000 }, at: NOW },
      { signal: { type: "offline-served", cachedAt: NOW - 120_000 }, at: NOW + 1 },
      { signal: { type: "offline-served", cachedAt: NOW - 30_000 }, at: NOW + 2 },
    ]);
    expect(state.cachedAt).toBe(NOW - 30_000);
    // First signal's timestamp anchors the pending delay.
    expect(state.pendingEnterAt).toBe(NOW);
  });

  it("null cachedAt enters after the delay without an age", () => {
    let state = run([{ signal: { type: "offline-served", cachedAt: null }, at: NOW }]);
    state = offlineReducer(state, { type: "enter-delay-elapsed" }, NOW + 2000);
    expect(state.offline).toBe(true);
    expect(state.cachedAt).toBeNull();
  });

  it("while already offline, refreshes cachedAt immediately", () => {
    let state = run([{ signal: { type: "navigator-offline" }, at: NOW }]);
    state = offlineReducer(state, { type: "enter-delay-elapsed" }, NOW + 2000);
    expect(state.offline).toBe(true);
    state = offlineReducer(state, { type: "offline-served", cachedAt: NOW - 10_000 }, NOW + 3000);
    expect(state.offline).toBe(true);
    expect(state.cachedAt).toBe(NOW - 10_000);
  });
});

describe("offlineReducer — recovery", () => {
  const offlineState = run([
    { signal: { type: "offline-served", cachedAt: NOW }, at: NOW },
    { signal: { type: "enter-delay-elapsed" }, at: NOW + 2000 },
  ]);

  it("fetch-success clears offline immediately and resets counters", () => {
    let state = offlineReducer(offlineState, { type: "fetch-success" }, NOW + 5000);
    expect(state.offline).toBe(false);
    expect(state.offlineSince).toBeNull();
    // A single subsequent failure must not re-trip (counters reset).
    state = offlineReducer(state, { type: "fetch-failure" }, NOW + 6000);
    expect(state.offline).toBe(false);
  });

  it("navigator-online clears offline immediately", () => {
    const state = offlineReducer(offlineState, { type: "navigator-online" }, NOW + 5000);
    expect(state.offline).toBe(false);
  });

  it("fetch-success cancels a pending navigator-offline enter", () => {
    const state = run([
      { signal: { type: "navigator-offline" }, at: NOW },
      { signal: { type: "fetch-success" }, at: NOW + 500 },
      { signal: { type: "enter-delay-elapsed" }, at: NOW + OFFLINE_ENTER_DELAY_MS },
    ]);
    expect(state.offline).toBe(false);
  });
});

describe("formatAsOf", () => {
  it("renders HH:MM:SS matching the Last sample convention", () => {
    expect(formatAsOf(NOW)).toMatch(/^\d{2}:\d{2}:\d{2}$/);
  });
});

describe("deriveLiveDataError", () => {
  const base = {
    isDemoMode: false,
    isOptionsWorkspace: false,
    browserOffline: false,
    portfolioError: null as string | null,
    ordersError: null as string | null,
    priceError: null as string | null,
  };

  it("browserOffline suppresses the degraded banner (offline banner is the single explanation)", () => {
    expect(
      deriveLiveDataError({ ...base, browserOffline: true, portfolioError: "Failed to fetch" }),
    ).toBeNull();
  });

  it("demo mode suppresses", () => {
    expect(deriveLiveDataError({ ...base, isDemoMode: true, portfolioError: "x" })).toBeNull();
  });

  it("options workspace suppresses", () => {
    expect(
      deriveLiveDataError({ ...base, isOptionsWorkspace: true, portfolioError: "x" }),
    ).toBeNull();
  });

  it("preserves portfolio ?? orders ?? price precedence", () => {
    expect(
      deriveLiveDataError({ ...base, portfolioError: "p", ordersError: "o", priceError: "w" }),
    ).toBe("p");
    expect(deriveLiveDataError({ ...base, ordersError: "o", priceError: "w" })).toBe("o");
    expect(deriveLiveDataError({ ...base, priceError: "w" })).toBe("w");
  });

  it("all clear → null", () => {
    expect(deriveLiveDataError(base)).toBeNull();
  });
});

describe("connectivity label overrides", () => {
  it("browser offline overrides any display status", () => {
    expect(resolveConnectivityLabel("relay_offline", true)).toEqual({
      text: "OFFLINE",
      cls: "dead",
    });
    expect(resolveConnectivityLabel("connected", true)).toEqual({
      text: "OFFLINE",
      cls: "dead",
    });
  });

  it("online → null (caller falls back to statusLabel)", () => {
    expect(resolveConnectivityLabel("relay_offline", false)).toBeNull();
    expect(resolveConnectivityLabel("connected", false)).toBeNull();
  });

  it("mobile chip override when offline, null when online", () => {
    expect(resolveMobileConnectivityChip(true)).toEqual({
      text: "OFF",
      cls: "offline",
      aria: "Device offline, showing last known data",
    });
    expect(resolveMobileConnectivityChip(false)).toBeNull();
  });
});
