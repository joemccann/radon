/**
 * Pure offline-status decision core. The provider in OfflineStatusContext.tsx
 * is a thin shell around `offlineReducer`; everything here is node-testable
 * with fixed literal timestamps (staleDataMachine pattern).
 *
 * Signal sources:
 * - window online/offline events (navigator-offline is debounced 2s so
 *   transient blips never flash the banner)
 * - the offlineSignals bus fed by data hooks (fetch-failure / fetch-success /
 *   offline-served)
 * - offline-served responses carry the SW's X-Radon-Offline marker: proof a
 *   real network fetch just failed. They arm the same 2s enter delay as
 *   navigator-offline (not an instant trip) so one blipped poll amid healthy
 *   traffic cannot flash the banner; a genuine outage enters 2s later with
 *   the cachedAt stamp preserved.
 */

export const OFFLINE_ENTER_DELAY_MS = 2000;
export const FAILURES_TO_TRIP = 2;
export const FAILURE_WINDOW_MS = 45_000;

export type OfflineSignal =
  | { type: "navigator-offline" }
  | { type: "navigator-online" }
  | { type: "enter-delay-elapsed" }
  | { type: "fetch-failure" }
  | { type: "fetch-success" }
  | { type: "offline-served"; cachedAt: number | null };

export type OfflineState = {
  offline: boolean;
  offlineSince: number | null;
  cachedAt: number | null;
  pendingEnterAt: number | null;
  failureTimes: number[];
};

export const INITIAL_OFFLINE_STATE: OfflineState = {
  offline: false,
  offlineSince: null,
  cachedAt: null,
  pendingEnterAt: null,
  failureTimes: [],
};

function enterOffline(state: OfflineState, now: number, cachedAt: number | null): OfflineState {
  return {
    offline: true,
    offlineSince: state.offline ? state.offlineSince : now,
    cachedAt: maxCachedAt(state.cachedAt, cachedAt),
    pendingEnterAt: null,
    failureTimes: [],
  };
}

function clearToOnline(): OfflineState {
  return { ...INITIAL_OFFLINE_STATE };
}

function maxCachedAt(a: number | null, b: number | null): number | null {
  if (a == null) return b;
  if (b == null) return a;
  return Math.max(a, b);
}

export function offlineReducer(
  state: OfflineState,
  signal: OfflineSignal,
  now: number,
): OfflineState {
  switch (signal.type) {
    case "navigator-offline":
      if (state.offline) return state;
      return { ...state, pendingEnterAt: now };
    case "enter-delay-elapsed": {
      if (state.pendingEnterAt == null) return state;
      if (now - state.pendingEnterAt < OFFLINE_ENTER_DELAY_MS) return state;
      return enterOffline(state, now, state.cachedAt);
    }
    case "fetch-failure": {
      const recent = state.failureTimes
        .filter((t) => now - t <= FAILURE_WINDOW_MS)
        .concat(now);
      if (recent.length >= FAILURES_TO_TRIP) return enterOffline(state, now, null);
      return { ...state, failureTimes: recent };
    }
    case "offline-served": {
      const cachedAt = maxCachedAt(state.cachedAt, signal.cachedAt);
      if (state.offline) return { ...enterOffline(state, now, cachedAt) };
      return { ...state, cachedAt, pendingEnterAt: state.pendingEnterAt ?? now };
    }
    case "navigator-online":
    case "fetch-success":
      return clearToOnline();
  }
}

type HeaderReader = { get(name: string): string | null };

/**
 * Reads the SW offline markers off a Response's headers. The caching layer
 * stamps X-Radon-Cached-At as an ISO string (sw-decisions.js
 * buildCachedAtStamp); numeric epoch-ms is accepted too. Garbage → null.
 * Tolerates mocked responses without a headers object.
 */
export function readOfflineMeta(
  headers: HeaderReader | null | undefined,
): { servedOffline: boolean; cachedAt: number | null } {
  if (!headers || typeof headers.get !== "function") {
    return { servedOffline: false, cachedAt: null };
  }
  const servedOffline = headers.get("X-Radon-Offline") === "1";
  const raw = headers.get("X-Radon-Cached-At");
  if (!raw) return { servedOffline, cachedAt: null };
  const parsed = /^\d+$/.test(raw.trim()) ? Number(raw.trim()) : Date.parse(raw);
  return { servedOffline, cachedAt: Number.isFinite(parsed) ? parsed : null };
}

/** Matches the existing `Last sample HH:MM:SS` convention in WorkspaceShell. */
export function formatAsOf(cachedAtMs: number): string {
  return new Date(cachedAtMs).toLocaleTimeString([], { hour12: false });
}

/**
 * The live-data-degraded banner condition, extracted from WorkspaceShell.
 * `browserOffline` suppresses it: the offline banner is the single
 * explanation, raw "Failed to fetch" text must not double-render.
 */
export function deriveLiveDataError(input: {
  isDemoMode: boolean;
  isOptionsWorkspace: boolean;
  browserOffline: boolean;
  portfolioError: string | null;
  ordersError: string | null;
  priceError: string | null;
}): string | null {
  if (input.isDemoMode || input.isOptionsWorkspace || input.browserOffline) return null;
  return input.portfolioError ?? input.ordersError ?? input.priceError;
}

/**
 * Sidebar/footer status override: a client-side outage must read OFFLINE, not
 * "RELAY OFFLINE" (deriveDisplayStatus can't tell the difference). Returns
 * null when online so callers fall back to their existing labels.
 */
export function resolveConnectivityLabel(
  _displayStatus: string,
  browserOffline: boolean,
): { text: "OFFLINE"; cls: "dead" } | null {
  if (!browserOffline) return null;
  return { text: "OFFLINE", cls: "dead" };
}

/** MobileAppBar chip override when the device is offline. */
export function resolveMobileConnectivityChip(
  browserOffline: boolean,
): { text: "OFF"; cls: "offline"; aria: string } | null {
  if (!browserOffline) return null;
  return { text: "OFF", cls: "offline", aria: "Device offline, showing last known data" };
}
