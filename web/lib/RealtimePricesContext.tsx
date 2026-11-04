"use client";

/**
 * Root-owned realtime prices connection.
 *
 * App Router navigations remount every page's WorkspaceShell (pages render the
 * shell; only layouts persist), so a socket owned by the shell died on every
 * route change: close → fresh Clerk token → fresh ws-ticket → reconnect →
 * full resubscribe → snapshot resync. On mobile that turned every tab-bar
 * navigation into a network round-trip stall (2026-09-01).
 *
 * This provider owns the ONE usePrices instance for the life of the tab. It is
 * mounted in the root Providers tree, which persists across navigations; the
 * active page's shell only PUBLISHES what to stream via publishSubscriptions.
 * Route changes therefore swap views over the same open socket. Reconnect for
 * genuine drops (network loss, backgrounding, relay restart, IB MFA) is
 * unchanged — it all lives inside usePrices.
 *
 * Shrink linger: a remounting shell first publishes while its portfolio/orders
 * inputs are still re-resolving, which would unsubscribe every held symbol and
 * resubscribe seconds later — a snapshot resync through the side door. Growth
 * applies immediately; the first shrink opens a SUBSCRIPTION_SHRINK_LINGER_MS
 * window during which the set that was streaming when the window opened (the
 * baseline) stays applied alongside the latest publish. The window closes on
 * its original deadline and commits whatever is desired then
 * (`subscriptionLingerTransition`). Depth subjects are exempt: they
 * are a scarce relay resource (~3 tickets) and always track the latest publish
 * exactly.
 *
 * Regression pin: web/tests/realtime-prices-navigation-persistence.test.tsx.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { usePrices, type UsePricesReturn } from "./usePrices";
import { optionKey, type IndexContract, type OptionContract } from "./pricesProtocol";
import { useRealtimeAuth } from "./RealtimeAuthContext";
import {
  buildDemoRealtimeSample,
  buildDemoSnapshotPrices,
} from "./demo/demoRealtime";

export type RealtimeSubscriptionRequest = {
  symbols: string[];
  contracts: OptionContract[];
  indexes: IndexContract[];
  depthSymbol: string | null;
  depthSymbols: string[];
  depthExpiry: string | null;
};

/** How long a shrink of the L1 subscription set must remain the desired state
 *  before it is committed to the wire. Sized to cover the post-navigation gap
 *  where the new shell's portfolio/orders GETs are still in flight. */
export const SUBSCRIPTION_SHRINK_LINGER_MS = 5_000;
const DEMO_QUOTE_REFRESH_MS = 60_000;

const EMPTY_REQUEST: RealtimeSubscriptionRequest = {
  symbols: [],
  contracts: [],
  indexes: [],
  depthSymbol: null,
  depthSymbols: [],
  depthExpiry: null,
};

export type RealtimePricesValue = UsePricesReturn & {
  /** Replace the desired subscription set (last write wins). Publishers must
   *  NOT clear on unmount — the next page's shell overwrites instead, so a
   *  navigation never empties the set mid-swap. */
  publishSubscriptions: (next: RealtimeSubscriptionRequest) => void;
};

// Inert default so isolated component tests can render consumers without the
// provider. Production always mounts the provider (pinned by the ownership
// contract test), so this never carries live traffic.
const DEFAULT_VALUE: RealtimePricesValue = {
  prices: {},
  fundamentals: {},
  depths: {},
  tape: {},
  connected: false,
  ibConnected: false,
  // Fail closed: no provider means no evidence the requested data is healthy.
  marketDataDegraded: true,
  ibIssue: null,
  ibStatusMessage: null,
  error: null,
  reconnect: () => {},
  getSnapshot: async () => ({}),
  publishSubscriptions: () => {},
};

const RealtimePricesContext = createContext<RealtimePricesValue>(DEFAULT_VALUE);

const indexKeyOf = (idx: IndexContract) => `${idx.symbol}@${idx.exchange}`;

function unionBy<T>(previous: T[], next: T[], keyOf: (item: T) => string): T[] {
  const seen = new Set(next.map(keyOf));
  const merged = [...next];
  for (const item of previous) {
    const key = keyOf(item);
    if (!seen.has(key)) {
      seen.add(key);
      merged.push(item);
    }
  }
  return merged;
}

const l1Key = (r: RealtimeSubscriptionRequest) =>
  [...r.symbols].sort().join(",") +
  "|" + r.contracts.map(optionKey).sort().join(",") +
  "|" + r.indexes.map(indexKeyOf).sort().join(",");

/** True when the two requests stream the same L1 set (depth excluded — depth
 *  always passes through exactly). */
function sameL1Set(a: RealtimeSubscriptionRequest, b: RealtimeSubscriptionRequest): boolean {
  return l1Key(a) === l1Key(b);
}

/** Full content key, depth included. Publishers hand fresh array identities on
 *  every render (memo chains recompute), so publish must diff by CONTENT: an
 *  identity-only republish is a no-op or the provider's setState re-renders
 *  the publisher and loops forever. */
const requestKey = (r: RealtimeSubscriptionRequest) =>
  l1Key(r) +
  "||" + (r.depthSymbol ?? "") +
  "|" + [...r.depthSymbols].sort().join(",") +
  "|" + (r.depthExpiry ?? "");

/** Shrink-linger machine. `settled`: the wire carries exactly the desired
 *  set. `lingering`: a shrink is pending; the wire carries baseline ∪ desired,
 *  where baseline is frozen at the moment the window opened. Freezing it keeps
 *  every subject that was streaming before the churn alive for the whole
 *  window (A→B→C no longer drops A on the C publish) while bounding the
 *  applied set to two publishes' worth (REL-197 / R-563: never an
 *  accumulated union across windows). */
export type SubscriptionLingerState =
  | { phase: "settled"; applied: RealtimeSubscriptionRequest }
  | {
      phase: "lingering";
      baseline: RealtimeSubscriptionRequest;
      desired: RealtimeSubscriptionRequest;
      applied: RealtimeSubscriptionRequest;
    };

export type SubscriptionLingerEvent =
  | { type: "publish"; next: RealtimeSubscriptionRequest }
  /** The window's deadline passed: commit the desired set. */
  | { type: "expire" };

function withBaseline(
  baseline: RealtimeSubscriptionRequest,
  next: RealtimeSubscriptionRequest,
): RealtimeSubscriptionRequest {
  return {
    symbols: unionBy(baseline.symbols, next.symbols, (s) => s),
    contracts: unionBy(baseline.contracts, next.contracts, optionKey),
    indexes: unionBy(baseline.indexes, next.indexes, indexKeyOf),
    depthSymbol: next.depthSymbol,
    depthSymbols: next.depthSymbols,
    depthExpiry: next.depthExpiry,
  };
}

export function subscriptionLingerTransition(
  state: SubscriptionLingerState,
  event: SubscriptionLingerEvent,
): SubscriptionLingerState {
  if (event.type === "expire") {
    return state.phase === "lingering" ? { phase: "settled", applied: state.desired } : state;
  }
  const { next } = event;
  const baseline = state.phase === "lingering" ? state.baseline : state.applied;
  const applied = withBaseline(baseline, next);
  if (sameL1Set(applied, next)) {
    // Nothing would be removed (pure growth, or the churn came back to a
    // superset of the baseline): the window, if any, has nothing to protect.
    return { phase: "settled", applied: next };
  }
  return { phase: "lingering", baseline, desired: next, applied };
}

export function RealtimePricesProvider({ children }: { children: ReactNode }) {
  const getToken = useRealtimeAuth();
  const demoMode = process.env.NEXT_PUBLIC_RADON_DEMO === "1";
  const [applied, setApplied] = useState<RealtimeSubscriptionRequest>(EMPTY_REQUEST);
  const [demoClock, setDemoClock] = useState(() => new Date());
  const lingerRef = useRef<SubscriptionLingerState>({ phase: "settled", applied: EMPTY_REQUEST });
  const shrinkTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const desiredKeyRef = useRef<string>(requestKey(EMPTY_REQUEST));

  const applyLinger = useCallback((event: SubscriptionLingerEvent) => {
    const prev = lingerRef.current;
    const next = subscriptionLingerTransition(prev, event);
    lingerRef.current = next;
    if (next.applied !== prev.applied) setApplied(next.applied);
    if (next.phase === "settled" && shrinkTimerRef.current !== null) {
      clearTimeout(shrinkTimerRef.current);
      shrinkTimerRef.current = null;
    }
    // The deadline is MONOTONIC: armed when a window opens, never re-armed by
    // a later publish inside it, so a desired set changing faster than the
    // linger still commits its unsubscribes within one window.
    if (next.phase === "lingering" && shrinkTimerRef.current === null) {
      shrinkTimerRef.current = setTimeout(() => {
        shrinkTimerRef.current = null;
        applyLingerRef.current({ type: "expire" });
      }, SUBSCRIPTION_SHRINK_LINGER_MS);
    }
  }, []);
  const applyLingerRef = useRef(applyLinger);
  applyLingerRef.current = applyLinger;

  const publishSubscriptions = useCallback((next: RealtimeSubscriptionRequest) => {
    // Content-identical republish (a consumer re-rendered with fresh array
    // identities): leave everything alone, including a pending shrink timer —
    // resetting it here would postpone genuine shrinks forever under churn.
    const nextKey = requestKey(next);
    if (nextKey === desiredKeyRef.current) return;
    desiredKeyRef.current = nextKey;
    applyLinger({ type: "publish", next });
  }, [applyLinger]);

  useEffect(() => () => {
    if (shrinkTimerRef.current) clearTimeout(shrinkTimerRef.current);
  }, []);

  useEffect(() => {
    if (!demoMode) return;
    const refresh = () => setDemoClock(new Date());
    refresh();
    const interval = setInterval(refresh, DEMO_QUOTE_REFRESH_MS);
    return () => clearInterval(interval);
  }, [demoMode]);

  const liveRealtime = usePrices({
    symbols: applied.symbols,
    contracts: applied.contracts,
    indexes: applied.indexes,
    enabled: !demoMode,
    depthSymbol: applied.depthSymbol,
    depthSymbols: applied.depthSymbols,
    depthExpiry: applied.depthExpiry,
    getToken,
  });
  const demoSample = useMemo(
    () => buildDemoRealtimeSample(applied, demoClock),
    [applied, demoClock],
  );
  const demoGetSnapshot = useCallback(
    async (symbols: string[]) => buildDemoSnapshotPrices(symbols, new Date()),
    [],
  );
  const demoReconnect = useCallback(() => {}, []);
  const realtime: UsePricesReturn = demoMode
    ? {
        ...demoSample,
        connected: false,
        ibConnected: false,
        marketDataDegraded: false,
        ibIssue: null,
        ibStatusMessage: null,
        error: null,
        reconnect: demoReconnect,
        getSnapshot: demoGetSnapshot,
      }
    : liveRealtime;

  return (
    <RealtimePricesContext.Provider value={{ ...realtime, publishSubscriptions }}>
      {children}
    </RealtimePricesContext.Provider>
  );
}

export function useRealtimePrices(): RealtimePricesValue {
  return useContext(RealtimePricesContext);
}
