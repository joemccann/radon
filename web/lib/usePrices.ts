"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  type WSMessage,
  type PriceData,
  type FundamentalsData,
  type DepthBook,
  type Trade,
  type OptionContract,
  type IndexContract,
  normalizeSymbolList,
  uniqueOptionContracts,
  symbolKey,
  contractsKey,
  optionKey,
  parseOptionKey,
} from "./pricesProtocol";
import { createReconnectStrategy, type ReconnectState } from "./reconnectStrategy";
import { ticketBlockedMs } from "./wsTicket";
import { buildAuthenticatedWebSocketUrl, resolveRealtimeWebSocketUrl } from "./realtimeSocketAuth";
import { REALTIME_OPEN_TIMEOUT_MS } from "./realtimeDeadline";
import type { RealtimeTokenGetter } from "./RealtimeAuthContext";

export type PriceUpdate = {
  symbol: string;
  data: PriceData;
  receivedAt: Date;
};

/** Mirrors the relay's MAX_CONCURRENT_DEPTH guard. */
export const MAX_FOCUSED_DEPTH_SUBJECTS = 3;

export type UsePricesOptions = {
  /** Symbols to subscribe to (stock tickers) */
  symbols: string[];
  /** Option contracts to subscribe to */
  contracts?: OptionContract[];
  /** Index contracts to subscribe to (e.g. VIX, VVIX) */
  indexes?: IndexContract[];
  /** Enable real-time streaming (default: true) */
  enabled?: boolean;
  /**
   * The single symbol whose L2 depth-of-book should stream. Depth is a
   * scarce resource (~3 concurrent tickets), so only the focused subject
   * subscribes. `null`/`undefined` releases any active depth ticket.
   * NOTE: this alone never forces a connection — the focused symbol is
   * already part of `symbols`/`contracts`.
   */
  depthSymbol?: string | null;
  /** Multiple simultaneous depth subjects. Used by two-leg implied spread books.
   * Takes precedence over `depthSymbol`; bounded by the relay's ticket cap. */
  depthSymbols?: string[];
  /**
   * For a futures/index depth subject, the order-ticket selected contract's
   * expiry (YYYYMMDD or YYYYMM). The depth KEY stays `depthSymbol`; this expiry
   * tells the relay WHICH listed future to resolve under that key. `null`
   * resolves the front month. Ignored for OPTION depth subjects (those carry
   * their own structured expiry/strike/right).
   */
  depthExpiry?: string | null;
  /** Callback when a price updates */
  onPriceUpdate?: (update: PriceUpdate) => void;
  /** Callback when connection status changes */
  onConnectionChange?: (connected: boolean) => void;
  /** Clerk getToken function for WebSocket auth */
  getToken?: RealtimeTokenGetter;
};

export type UsePricesReturn = {
  /** REL-318: requested market data is degraded while the socket may be open. */
  marketDataDegraded?: boolean;
  /** Current prices keyed by symbol */
  prices: Record<string, PriceData>;
  /** Fundamentals data keyed by symbol (from IB generic tick 258) */
  fundamentals: Record<string, FundamentalsData>;
  /** Depth-of-book (L2) keyed by symbol. Only the focused `depthSymbol` populates. */
  depths: Record<string, DepthBook>;
  /** Time & Sales tape keyed by symbol (newest-first, bounded). Rides the same
   *  focused `depthSymbol` as `depths` — only that subject populates. */
  tape: Record<string, Trade[]>;
  /** Whether the connection is active */
  connected: boolean;
  /** Whether IB is connected on the server */
  ibConnected: boolean;
  /** Structured IB-side issue code from the realtime server, when available */
  ibIssue: string | null;
  /** Operator-facing IB-side status guidance, when available */
  ibStatusMessage: string | null;
  /** Any error message */
  error: string | null;
  /** Manually reconnect */
  reconnect: () => void;
  /** Get a snapshot for symbols (doesn't require streaming connection) */
  getSnapshot: (symbols: string[]) => Promise<Record<string, PriceData>>;
};

/** What the relay last said about the broker on THIS socket. A fresh socket
 *  starts `unknown`: the previous socket's report says nothing about the new
 *  relay session (audit §1 #15). */
export type BrokerReport =
  | { kind: "unknown" }
  | {
      kind: "reported";
      ibConnected: boolean;
      marketDataDegraded: boolean;
      ibIssue: string | null;
      ibStatusMessage: string | null;
    };

/** Price-socket connection machine. Broker state only exists while open, so a
 *  closed socket cannot carry a stale `ibConnected` into the next session. */
export type PriceFeedState =
  | { phase: "idle" }
  | { phase: "connecting" }
  | { phase: "open"; broker: BrokerReport }
  | { phase: "closed" };

export type PriceFeedEvent =
  /** A connect attempt starts (auth + socket construction). */
  | { type: "connect" }
  /** The socket reached OPEN. */
  | { type: "open" }
  /** Relay status frame. */
  | {
      type: "status";
      ibConnected: boolean;
      marketDataDegraded: boolean;
      ibIssue: string | null;
      ibStatusMessage: string | null;
    }
  /** Socket dropped, errored, or auth failed: reconnect is eligible. */
  | { type: "closed" }
  /** Teardown or an abandoned attempt: back to idle. */
  | { type: "reset" };

export function priceFeedTransition(state: PriceFeedState, event: PriceFeedEvent): PriceFeedState {
  switch (event.type) {
    case "connect":
      return { phase: "connecting" };
    case "open":
      return state.phase === "connecting" ? { phase: "open", broker: { kind: "unknown" } } : state;
    case "status":
      if (state.phase !== "open") return state;
      return {
        phase: "open",
        broker: {
          kind: "reported",
          ibConnected: event.ibConnected,
          marketDataDegraded: event.marketDataDegraded,
          ibIssue: event.ibIssue,
          ibStatusMessage: event.ibStatusMessage,
        },
      };
    case "closed":
      return state.phase === "closed" ? state : { phase: "closed" };
    case "reset":
      return state.phase === "idle" ? state : { phase: "idle" };
  }
}

export type PriceFeedView = {
  connected: boolean;
  ibConnected: boolean;
  marketDataDegraded: boolean;
  ibIssue: string | null;
  ibStatusMessage: string | null;
};

/** Flat view the hook returns. An unknown broker reads as NOT connected, so
 *  `connected && ibConnected && !marketDataDegraded` stays closed until the
 *  current socket's relay reports. */
export function derivePriceFeedView(state: PriceFeedState): PriceFeedView {
  if (state.phase !== "open") {
    return { connected: false, ibConnected: false, marketDataDegraded: false, ibIssue: null, ibStatusMessage: null };
  }
  const { broker } = state;
  if (broker.kind === "unknown") {
    return { connected: true, ibConnected: false, marketDataDegraded: false, ibIssue: null, ibStatusMessage: null };
  }
  return {
    connected: true,
    ibConnected: broker.ibConnected,
    marketDataDegraded: broker.marketDataDegraded,
    ibIssue: broker.ibIssue,
    ibStatusMessage: broker.ibStatusMessage,
  };
}

const WS_DEBUG = process.env.NODE_ENV === "development";
function wsLog(...args: unknown[]) {
  if (WS_DEBUG) console.debug("[usePrices]", ...args);
}

const STALENESS_CHECK_INTERVAL_MS = 15_000;
const STALENESS_THRESHOLD_MS = 60_000;

/** Time & Sales tape is bounded per symbol so a busy print stream can't grow
 *  unboundedly. Newest rows are kept (the tape renders newest-first). */
const TAPE_MAX_PER_SYMBOL = 50;

/**
 * React hook for real-time price streaming from IB via WebSocket.
 *
 * Uses a connection state machine to prevent teardown/recreate cycles
 * when subscriptions change. Subscriptions are synced via diff-based
 * messages over the existing connection.
 */
export function usePrices(options: UsePricesOptions): UsePricesReturn {
  const {
    symbols,
    contracts = [],
    indexes = [],
    enabled = true,
    depthSymbol = null,
    depthSymbols = [],
    depthExpiry = null,
    onPriceUpdate,
    onConnectionChange,
    getToken,
  } = options;

  const [prices, setPrices] = useState<Record<string, PriceData>>({});
  const [fundamentals, setFundamentals] = useState<Record<string, FundamentalsData>>({});
  const [depths, setDepths] = useState<Record<string, DepthBook>>({});
  const [tape, setTape] = useState<Record<string, Trade[]>>({});
  const [feed, setFeed] = useState<PriceFeedState>({ phase: "idle" });
  const [error, setError] = useState<string | null>(null);

  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const openTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const stalenessTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const lastMessageRef = useRef<number>(Date.now());
  const mountedRef = useRef(true);
  const enabledRef = useRef(enabled);

  // Connection state machine. The ref is the synchronous source of truth for
  // the guards below; `feed` mirrors it for rendering.
  const feedRef = useRef<PriceFeedState>({ phase: "idle" });
  const dispatchFeed = useCallback((event: PriceFeedEvent) => {
    const next = priceFeedTransition(feedRef.current, event);
    if (next === feedRef.current) return;
    feedRef.current = next;
    setFeed(next);
  }, []);
  const socketGenRef = useRef(0);

  // Reconnect strategy (shared utility)
  const reconnectStrategyRef = useRef<ReconnectState>(createReconnectStrategy());

  // Desired subscription tracking (ref — not rendered)
  const desiredRef = useRef<{
    symbols: string[];
    contracts: OptionContract[];
    indexes: IndexContract[];
  }>({ symbols: [], contracts: [], indexes: [] });
  const lastSentHashRef = useRef("");

  // Focused-depth tracking — kept SEPARATE from the price subscription diff so
  // the scarce depth resource is routed on its own. We track the symbol AND the
  // optional selected future expiry separately, but dedupe/diff on the PAIR
  // (symbol + "|" + expiry) so changing ONLY the expiry still re-fires
  // subscribe-depth (the relay swaps the resolved contract under the same key).
  const desiredDepthRef = useRef<string[]>([]);
  const desiredDepthExpiryRef = useRef<string | null>(null);
  const lastSentDepthKeysRef = useRef<string[]>([]);

  // Callback refs (avoid stale closures in WS handlers)
  const onPriceUpdateRef = useRef(onPriceUpdate);
  const onConnectionChangeRef = useRef(onConnectionChange);
  const getTokenRef = useRef(getToken);

  // Stable hashes for change detection
  const symbolHash = symbolKey(symbols);
  const contractHash = contractsKey(contracts);
  const indexHash = useMemo(
    () => indexes.map((i) => `${i.symbol}@${i.exchange}`).sort().join(","),
    [indexes],
  );
  // Hash keys avoid recomputation when parents pass new array identities with the same subscription set.
  const normalizedSymbols = useMemo(
    () => normalizeSymbolList(symbols),
    [symbolHash], // eslint-disable-line react-hooks/exhaustive-deps -- symbolHash content-keys `symbols`
  );
  const normalizedContracts = useMemo(
    () => uniqueOptionContracts(contracts),
    [contractHash], // eslint-disable-line react-hooks/exhaustive-deps -- contractHash content-keys `contracts`
  );
  const normalizedIndexes = useMemo(
    () => indexes,
    [indexHash], // eslint-disable-line react-hooks/exhaustive-deps -- indexHash content-keys `indexes`
  );

  const hasSubscriptions =
    normalizedSymbols.length > 0 ||
    normalizedContracts.length > 0 ||
    normalizedIndexes.length > 0;
  const hasSubscriptionsRef = useRef(hasSubscriptions);

  const normalizedDepthSymbol =
    depthSymbol && depthSymbol.trim().length > 0 ? depthSymbol.trim() : null;
  const depthSymbolsHash = depthSymbols.map((symbol) => symbol.trim()).filter(Boolean).sort().join(",");
  const normalizedDepthSymbols = useMemo(() => {
    const requested = depthSymbols.length > 0
      ? depthSymbols
      : normalizedDepthSymbol
        ? [normalizedDepthSymbol]
        : [];
    return [...new Set(requested.map((symbol) => symbol.trim()).filter(Boolean))]
      .sort()
      .slice(0, MAX_FOCUSED_DEPTH_SUBJECTS);
  }, [depthSymbolsHash, normalizedDepthSymbol]); // eslint-disable-line react-hooks/exhaustive-deps -- content hash stabilizes caller arrays
  const normalizedDepthExpiry =
    depthExpiry && depthExpiry.trim().length > 0 ? depthExpiry.trim() : null;

  // Sync refs during render (before any useCallback/useEffect)
  desiredRef.current = {
    symbols: normalizedSymbols,
    contracts: normalizedContracts,
    indexes: normalizedIndexes,
  };
  enabledRef.current = enabled;
  hasSubscriptionsRef.current = hasSubscriptions;
  desiredDepthRef.current = normalizedDepthSymbols;
  desiredDepthExpiryRef.current = normalizedDepthExpiry;
  onPriceUpdateRef.current = onPriceUpdate;
  onConnectionChangeRef.current = onConnectionChange;
  getTokenRef.current = getToken;

  const socketUrl = resolveRealtimeWebSocketUrl();

  const clearReconnectTimer = useCallback(() => {
    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current);
      reconnectTimeoutRef.current = null;
    }
  }, []);

  const clearStalenessTimer = useCallback(() => {
    if (stalenessTimerRef.current) {
      clearInterval(stalenessTimerRef.current);
      stalenessTimerRef.current = null;
    }
  }, []);

  const clearOpenTimer = useCallback(() => {
    if (openTimeoutRef.current) {
      clearTimeout(openTimeoutRef.current);
      openTimeoutRef.current = null;
    }
  }, []);

  const buildHash = useCallback(
    (syms: string[], cts: OptionContract[], idxs: IndexContract[]) =>
      symbolKey(syms) +
      "|" +
      contractsKey(cts) +
      "|" +
      idxs
        .map((i) => `${i.symbol}@${i.exchange}`)
        .sort()
        .join(","),
    [],
  );

  /** Send diff-based subscribe/unsubscribe over an open socket. */
  const syncSubscriptions = useCallback(
    (ws: WebSocket) => {
      if (ws.readyState !== WebSocket.OPEN) return;

      const desired = desiredRef.current;
      const currentHash = buildHash(desired.symbols, desired.contracts, desired.indexes);

      if (currentHash === lastSentHashRef.current) return; // No change

      // Parse last-sent state to compute diff
      const [lastSyms = "", lastCts = "", lastIdxs = ""] = lastSentHashRef.current.split("|");
      const prevSymbolSet = new Set(lastSyms.split(",").filter(Boolean));
      const prevContractSet = new Set(lastCts.split(",").filter(Boolean));
      const prevIndexSet = new Set(lastIdxs.split(",").filter(Boolean));

      const currSymbolSet = new Set(desired.symbols);
      const currContractKeys = desired.contracts.map(optionKey);
      const currContractSet = new Set(currContractKeys);
      const currIndexPairs = desired.indexes
        .map((idx) => `${idx.symbol}@${idx.exchange}`)
        .sort();
      const currIndexSet = new Set(currIndexPairs);

      // Compute adds
      const addedSymbols = desired.symbols.filter((s) => !prevSymbolSet.has(s));
      const addedContracts = desired.contracts.filter(
        (c) => !prevContractSet.has(optionKey(c)),
      );

      // Compute removes
      const removedSymbols = [...prevSymbolSet].filter((s) => !currSymbolSet.has(s));
      const removedContractKeys = [...prevContractSet].filter(
        (k) => !currContractSet.has(k),
      );
      const removedIndexKeys = [...prevIndexSet].filter((k) => !currIndexSet.has(k));
      const removedIndexSymbols = [...new Set(removedIndexKeys.map((indexKey) => indexKey.split("@")[0]))];

      const addedIndexes = desired.indexes.filter(
        (idx) => !prevIndexSet.has(`${idx.symbol}@${idx.exchange}`),
      );

      wsLog("sync-diff", {
        addedSymbols,
        addedContracts: addedContracts.map(optionKey),
        removedSymbols,
        removedContractKeys,
        addedIndexes,
        removedIndexSymbols,
      });

      // Subscribe new (including any newly-added indexes)
      if (
        addedSymbols.length > 0 ||
        addedContracts.length > 0 ||
        addedIndexes.length > 0
      ) {
        ws.send(
          JSON.stringify({
            action: "subscribe",
            symbols: addedSymbols,
            ...(addedContracts.length > 0
              ? { contracts: addedContracts }
              : {}),
            ...(addedIndexes.length > 0
              ? { indexes: addedIndexes }
              : {}),
          }),
        );
      }

      // Unsubscribe old
      if (
        removedSymbols.length > 0 ||
        removedContractKeys.length > 0 ||
        removedIndexSymbols.length > 0
      ) {
        ws.send(
          JSON.stringify({
            action: "unsubscribe",
            symbols: [
              ...removedSymbols,
              ...removedContractKeys,
              ...removedIndexSymbols,
            ],
          }),
        );
        // Evict stale price entries
        setPrices((prev) => {
          const next = { ...prev };
          for (const k of [
            ...removedSymbols,
            ...removedContractKeys,
            ...removedIndexSymbols,
          ]) {
            delete next[k];
          }
          return next;
        });
      }

      lastSentHashRef.current = currentHash;
    },
    [buildHash],
  );

  /** Diff the bounded set of focused depth subjects. A normal book has one;
   * an implied two-leg spread has two. Unchanged books remain resident. */
  const syncDepth = useCallback((ws: WebSocket) => {
    if (ws.readyState !== WebSocket.OPEN) return;

    // A background tab holds no depth: the relay's few tickets are shared by
    // every session, so hidden tabs must not starve the one being looked at.
    const desired = typeof document !== "undefined" && document.visibilityState === "hidden"
      ? []
      : desiredDepthRef.current;
    const desiredExpiry = desiredDepthExpiryRef.current;
    const desiredKeys = desired.map((symbol) =>
      `${symbol}|${desired.length === 1 ? desiredExpiry ?? "" : ""}`,
    );
    const previousKeys = lastSentDepthKeysRef.current;
    if (desiredKeys.join(",") === previousKeys.join(",")) return;

    const desiredKeySet = new Set(desiredKeys);
    const previousKeySet = new Set(previousKeys);
    const removedKeys = previousKeys.filter((key) => !desiredKeySet.has(key));
    const addedKeys = desiredKeys.filter((key) => !previousKeySet.has(key));
    const desiredSymbols = new Set(desired);
    const removedSymbols = [...new Set(removedKeys.map((key) => key.split("|")[0]))]
      .filter((symbol) => !desiredSymbols.has(symbol));

    for (const previousSymbol of removedSymbols) {
      ws.send(
        JSON.stringify({ action: "unsubscribe-depth", symbol: previousSymbol }),
      );
    }
    if (removedSymbols.length > 0) {
      setDepths((prev) => Object.fromEntries(
        Object.entries(prev).filter(([symbol]) => !removedSymbols.includes(symbol)),
      ));
      setTape((prev) => Object.fromEntries(
        Object.entries(prev).filter(([symbol]) => !removedSymbols.includes(symbol)),
      ));
    }

    for (const desiredKey of addedKeys) {
      const [desiredSymbol, expiry = ""] = desiredKey.split("|");
      // A focused single-leg OPTION subject is keyed by its composite option
      // key (SYMBOL_YYYYMMDD_STRIKE_RIGHT). The relay's option-depth branch
      // needs the STRUCTURED contract fields (expiry/strike/right) to build the
      // OPRA montage — given only the composite string it falls through to a
      // bogus stock contract and emits depth-unavailable, so the panel degrades
      // to the L1 fallback. Decompose the key here so the relay re-derives the
      // SAME key via its own optionKey() and echoes the book under it.
      const optionContract = parseOptionKey(desiredSymbol);
      let payload: Record<string, unknown>;
      if (optionContract) {
        payload = {
          action: "subscribe-depth",
          symbol: optionContract.symbol,
          expiry: optionContract.expiry,
          strike: optionContract.strike,
          right: optionContract.right,
        };
      } else if (expiry) {
        // Futures/index subject with a selected expiry: tell the relay to
        // resolve THIS listed future (not the front month) under the same key.
        payload = {
          action: "subscribe-depth",
          symbol: desiredSymbol,
          instrument: "future",
          expiry,
        };
      } else {
        payload = { action: "subscribe-depth", symbol: desiredSymbol };
      }
      ws.send(JSON.stringify(payload));
    }

    lastSentDepthKeysRef.current = desiredKeys;
  }, []);

  // scheduleReconnect — ref-based to break circular dep with connect
  const scheduleReconnectRef = useRef<() => void>(() => {});

  // connect — idempotent, state-machine-guarded
  const connect = useCallback(() => {
    if (!enabled) return;
    const { symbols: syms, contracts: cts, indexes: idxs } = desiredRef.current;
    if (syms.length === 0 && cts.length === 0 && idxs.length === 0) return;

    // Idempotent: no-op if already connecting or open
    if (
      feedRef.current.phase === "connecting" ||
      feedRef.current.phase === "open"
    ) {
      wsLog("connect-noop", feedRef.current.phase);
      return;
    }

    clearReconnectTimer();

    const gen = ++socketGenRef.current;
    dispatchFeed({ type: "connect" });
    wsLog("connect", { gen });

    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }

    (async () => {
      let url: string;
      try {
        url = await buildAuthenticatedWebSocketUrl(socketUrl, getTokenRef.current);
      } catch (err) {
        if (gen !== socketGenRef.current || !mountedRef.current) return;
        dispatchFeed({ type: "closed" });
        const message = err instanceof Error ? err.message : "Realtime auth failed";
        setError(message);
        wsLog("Failed to authenticate realtime socket:", message);
        scheduleReconnectRef.current();
        return;
      }
      if (gen !== socketGenRef.current || !mountedRef.current) return;
      if (!enabledRef.current || !hasSubscriptionsRef.current) {
        // Abandoned attempt: leave "connecting" so a later connect() is not a no-op.
        dispatchFeed({ type: "reset" });
        return;
      }

      const ws = new WebSocket(url);
      wsRef.current = ws;
      clearOpenTimer();
      openTimeoutRef.current = setTimeout(() => {
        if (
          gen === socketGenRef.current &&
          mountedRef.current &&
          ws.readyState === WebSocket.CONNECTING
        ) {
          setError("Realtime connection timed out");
          ws.close();
        }
      }, REALTIME_OPEN_TIMEOUT_MS);

    ws.onopen = () => {
      if (gen !== socketGenRef.current || !mountedRef.current) return;
      clearOpenTimer();
      dispatchFeed({ type: "open" });
      reconnectStrategyRef.current.reset(); // Reset backoff on success
      lastMessageRef.current = Date.now();
      setError(null);
      onConnectionChangeRef.current?.(true);
      // Force full send on new connection
      lastSentHashRef.current = "";
      syncSubscriptions(ws);
      // Depth resubscribes from scratch on every fresh socket.
      lastSentDepthKeysRef.current = [];
      syncDepth(ws);
      wsLog("open", { gen });

      // Start staleness check
      clearStalenessTimer();
      stalenessTimerRef.current = setInterval(() => {
        if (Date.now() - lastMessageRef.current > STALENESS_THRESHOLD_MS) {
          wsLog("stale-connection", { silentMs: Date.now() - lastMessageRef.current });
          ws.close();
        }
      }, STALENESS_CHECK_INTERVAL_MS);
    };

    ws.onmessage = (event) => {
      if (gen !== socketGenRef.current || !mountedRef.current) return;
      lastMessageRef.current = Date.now();
      try {
        const message = JSON.parse(event.data as string) as WSMessage;

        switch (message.type) {
          case "price":
          case "snapshot": {
            const { data } = message;
            setPrices((prev) => ({
              ...prev,
              [data.symbol]: data,
            }));
            onPriceUpdateRef.current?.({
              symbol: data.symbol,
              data,
              receivedAt: new Date(),
            });
            break;
          }
          case "batch": {
            const { updates } = message;
            setPrices((prev) => ({ ...prev, ...updates }));
            const now = new Date();
            for (const [sym, data] of Object.entries(updates)) {
              onPriceUpdateRef.current?.({ symbol: sym, data, receivedAt: now });
            }
            break;
          }
          case "fundamentals": {
            const { symbol: fundSymbol, data: fundData } = message;
            setFundamentals((prev) => ({
              ...prev,
              [fundSymbol]: fundData,
            }));
            break;
          }
          case "depth": {
            const { data } = message;
            setDepths((prev) => ({ ...prev, [data.symbol]: data }));
            break;
          }
          case "depth-batch": {
            const { updates } = message;
            setDepths((prev) => ({ ...prev, ...updates }));
            break;
          }
          case "tape-batch": {
            // The relay sends each symbol's FULL ring-buffer snapshot
            // (oldest-first, already bounded), not a delta — so REPLACE, never
            // merge (merging would re-append the whole snapshot every flush and
            // duplicate rows). Keep oldest-first: classifyTicks walks the array
            // front-to-back treating each prior element as the chronologically
            // earlier print, and TimeAndSales reverses for newest-at-top display.
            const { updates } = message;
            setTape((prev) => {
              const next = { ...prev };
              for (const [sym, snapshot] of Object.entries(updates)) {
                next[sym] = snapshot.slice(-TAPE_MAX_PER_SYMBOL);
              }
              return next;
            });
            break;
          }
          case "depth-unavailable": {
            const { symbol: depthSym, reason } = message;
            if (reason === "recycled") {
              // The ticket was reassigned to another focused symbol; drop the
              // stale book entirely rather than render a non-entitled shell.
              setDepths((prev) => {
                if (!(depthSym in prev)) return prev;
                const next = { ...prev };
                delete next[depthSym];
                return next;
              });
              break;
            }
            // no-entitlement / futures-no-depth → mark the book unentitled so
            // the panel flips to the L1 fallback with a calm note.
            setDepths((prev) => ({
              ...prev,
              [depthSym]: {
                symbol: depthSym,
                kind: prev[depthSym]?.kind ?? "stock",
                bid: [],
                ask: [],
                isSmartDepth: prev[depthSym]?.isSmartDepth ?? false,
                feed: prev[depthSym]?.feed ?? null,
                entitled: false,
                timestamp: new Date().toISOString(),
              },
            }));
            break;
          }
          case "status":
            dispatchFeed({
              type: "status",
              ibConnected: message.ib_connected === true,
              marketDataDegraded: message.market_data_degraded === true,
              ibIssue: message.ib_issue ?? null,
              ibStatusMessage: message.ib_status_message ?? null,
            });
            break;
          case "error":
            setError(message.message);
            break;
          case "ping":
            if (ws.readyState === WebSocket.OPEN) {
              ws.send(JSON.stringify({ action: "pong" }));
            }
            break;
          case "pong":
          case "subscribed":
          case "unsubscribed":
            break;
          default:
            break;
        }
      } catch (error_) {
        console.error("Failed to parse price message:", error_);
      }
    };

    ws.onclose = () => {
      if (gen !== socketGenRef.current || !mountedRef.current) return;
      clearOpenTimer();
      dispatchFeed({ type: "closed" });
      clearStalenessTimer();
      onConnectionChangeRef.current?.(false);
      lastSentHashRef.current = ""; // Next connect must full-sync
      lastSentDepthKeysRef.current = []; // Depth re-subscribes on reconnect
      wsLog("close", { gen });
      scheduleReconnectRef.current();
    };

    ws.onerror = () => {
      if (gen !== socketGenRef.current || !mountedRef.current) return;
      dispatchFeed({ type: "closed" });
      setError("Connection lost");
      onConnectionChangeRef.current?.(false);
      wsLog("error", { gen });
      ws.close();
    };
    })();
  }, [enabled, socketUrl, clearReconnectTimer, clearStalenessTimer, clearOpenTimer, syncSubscriptions, syncDepth, dispatchFeed]);

  // Wire scheduleReconnect via ref to avoid circular dep
  const scheduleReconnect = useCallback(() => {
    if (!mountedRef.current || !enabled) return;
    const { symbols: syms, contracts: cts, indexes: idxs } = desiredRef.current;
    if (syms.length === 0 && cts.length === 0 && idxs.length === 0) return;

    const strategy = reconnectStrategyRef.current;
    if (!strategy.canRetry()) {
      // REL-197 (R-529): exhaustion is tab-lifetime now (the one usePrices
      // instance lives in root Providers), so last-known marks must not keep
      // rendering as live. Clearing the map routes every consumer to its
      // existing missing/"---" treatment; visibility/online/publish recovery
      // below re-arms the strategy.
      setError("Max reconnect attempts reached");
      setPrices({});
      return;
    }

    const delay = Math.max(strategy.nextDelay(), ticketBlockedMs());
    wsLog("reconnect-scheduled", { attempt: strategy.attempt, delay: Math.round(delay) });

    clearReconnectTimer();
    reconnectTimeoutRef.current = setTimeout(() => {
      if (mountedRef.current && enabled) {
        dispatchFeed({ type: "reset" }); // Allow connect()
        connect();
      }
    }, delay);
  }, [enabled, clearReconnectTimer, connect, dispatchFeed]);

  // Keep the ref in sync
  scheduleReconnectRef.current = scheduleReconnect;

  const reconnect = useCallback(() => {
    // Force re-entry into idle so connect() isn't a no-op
    dispatchFeed({ type: "reset" });
    reconnectStrategyRef.current.reset();
    connect();
  }, [connect, dispatchFeed]);

  // REL-197 (R-529): recover a closed/exhausted socket on the signals a
  // long-lived tab actually gets — returning to the foreground, the network
  // coming back, and a page publishing new symbols. Guarded to the closed
  // state with no backoff timer pending, so normal backoff is untouched.
  const recoverIfClosed = useCallback(() => {
    if (!enabled || !mountedRef.current) return;
    if (feedRef.current.phase !== "closed") return;
    if (reconnectTimeoutRef.current !== null) return;
    const { symbols: syms, contracts: cts, indexes: idxs } = desiredRef.current;
    if (syms.length === 0 && cts.length === 0 && idxs.length === 0) return;
    reconnect();
  }, [enabled, reconnect]);
  const recoverIfClosedRef = useRef(recoverIfClosed);
  recoverIfClosedRef.current = recoverIfClosed;
  const syncDepthRef = useRef(syncDepth);
  syncDepthRef.current = syncDepth;

  useEffect(() => {
    if (typeof document === "undefined" || typeof window === "undefined") return;
    const onVisible = () => {
      const ws = wsRef.current;
      if (ws && feedRef.current.phase === "open") syncDepthRef.current(ws);
      if (document.visibilityState === "visible") recoverIfClosedRef.current();
    };
    const onOnline = () => recoverIfClosedRef.current();
    document.addEventListener("visibilitychange", onVisible);
    window.addEventListener("online", onOnline);
    return () => {
      document.removeEventListener("visibilitychange", onVisible);
      window.removeEventListener("online", onOnline);
    };
  }, []);

  // getSnapshot — isolated WS, unchanged
  const getSnapshot = useCallback(
    async (snapshotSymbols: string[]): Promise<Record<string, PriceData>> => {
      const symbolsToRequest = normalizeSymbolList(snapshotSymbols);
      if (symbolsToRequest.length === 0) {
        return {};
      }

      return new Promise<Record<string, PriceData>>((resolve, reject) => {
        const results: Record<string, PriceData> = {};
        const pending = new Set(symbolsToRequest);
        let ws: WebSocket | null = null;
        let settled = false;

        const timeout = setTimeout(() => {
          settled = true;
          ws?.close();
          resolve(results);
        }, 5000);

        const wireSnapshotSocket = (socket: WebSocket) => {
          socket.onopen = () => {
            socket.send(
              JSON.stringify({ action: "snapshot", symbols: symbolsToRequest }),
            );
          };

          socket.onmessage = (event) => {
            try {
              const message = JSON.parse(event.data as string) as WSMessage;
              if (message.type === "snapshot") {
                const symbol = message.data.symbol.toUpperCase();
                results[symbol] = message.data;
                pending.delete(symbol);

                if (pending.size === 0) {
                  settled = true;
                  clearTimeout(timeout);
                  socket.close();
                  resolve(results);
                }
              } else if (message.type === "error") {
                settled = true;
                clearTimeout(timeout);
                socket.close();
                reject(new Error(message.message));
              }
            } catch (e) {
              console.error("Failed to parse message:", e);
            }
          };

          socket.onerror = () => {
            settled = true;
            clearTimeout(timeout);
            socket.close();
            reject(new Error("Failed to connect to price server"));
          };
        };

        buildAuthenticatedWebSocketUrl(socketUrl, getTokenRef.current)
          .then((url) => {
            if (settled || !mountedRef.current) return;
            ws = new WebSocket(url);
            wireSnapshotSocket(ws);
          })
          .catch((error_) => {
            if (settled) return;
            settled = true;
            clearTimeout(timeout);
            reject(error_);
          });
      }).catch((error_) => {
        setError(
          error_ instanceof Error ? error_.message : "Failed to get snapshot",
        );
        console.error("Snapshot error:", error_);
        return {};
      });
    },
    [socketUrl],
  );

  // Main lifecycle effect — connect/disconnect based on enabled + subscriptions
  useEffect(() => {
    mountedRef.current = true;

    if (enabled && hasSubscriptions) {
      connect();
    } else {
      // Teardown
      socketGenRef.current += 1;
      clearReconnectTimer();
      clearStalenessTimer();
      clearOpenTimer();
      if (wsRef.current) {
        if (wsRef.current.readyState !== WebSocket.CLOSED) {
          wsRef.current.close();
        }
        wsRef.current = null;
      }
      dispatchFeed({ type: "reset" });
      lastSentHashRef.current = "";
      lastSentDepthKeysRef.current = [];
      onConnectionChangeRef.current?.(false);
    }

    return () => {
      mountedRef.current = false;
      socketGenRef.current += 1;
      clearReconnectTimer();
      clearStalenessTimer();
      clearOpenTimer();
      if (wsRef.current) {
        if (wsRef.current.readyState !== WebSocket.CLOSED) {
          wsRef.current.close();
        }
        wsRef.current = null;
      }
      dispatchFeed({ type: "reset" });
      lastSentHashRef.current = "";
      lastSentDepthKeysRef.current = [];
    };
  }, [enabled, hasSubscriptions, connect, clearReconnectTimer, clearStalenessTimer, clearOpenTimer, dispatchFeed]);

  // Subscription sync effect — sends diffs over open connection
  useEffect(() => {
    const ws = wsRef.current;
    if (ws && feedRef.current.phase === "open") {
      syncSubscriptions(ws);
    } else if (feedRef.current.phase === "closed") {
      // REL-197 (R-529): a page publishing a changed desired set while the
      // socket sits exhausted is a recovery signal, not a no-op.
      recoverIfClosedRef.current();
    }
    // If still connecting, onopen will flush via syncSubscriptions
  }, [symbolHash, contractHash, indexHash, syncSubscriptions]);

  // Focused-depth sync effect — diffs the single depth symbol over the open
  // connection. Deliberately does NOT influence `hasSubscriptions`/connect:
  // the focused symbol is already streaming L1, so depth never forces a socket.
  useEffect(() => {
    const ws = wsRef.current;
    if (ws && feedRef.current.phase === "open") {
      syncDepth(ws);
    }
    // If still connecting, onopen will flush via syncDepth
  }, [depthSymbolsHash, normalizedDepthSymbol, normalizedDepthExpiry, syncDepth]);

  const feedView = useMemo(() => derivePriceFeedView(feed), [feed]);

  return {
    prices,
    fundamentals,
    depths,
    tape,
    ...feedView,
    error,
    reconnect,
    getSnapshot,
  };
}

/**
 * Format price for display
 */
export function formatPrice(price: number | null | undefined): string {
  if (price == null || Number.isNaN(price)) return "—";
  return price.toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

/**
 * Format volume for display
 */
export function formatVolume(volume: number | null | undefined): string {
  if (volume == null || Number.isNaN(volume)) return "—";
  if (volume >= 1_000_000) {
    return `${(volume / 1_000_000).toFixed(1)}M`;
  }
  if (volume >= 1_000) {
    return `${(volume / 1_000).toFixed(1)}K`;
  }
  return volume.toLocaleString();
}
