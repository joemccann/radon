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
import { buildAuthenticatedWebSocketUrl, resolveRealtimeWebSocketUrl } from "./realtimeSocketAuth";
import type { RealtimeTokenGetter } from "./RealtimeAuthContext";

export type PriceUpdate = {
  symbol: string;
  data: PriceData;
  receivedAt: Date;
};

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

type ConnState = "idle" | "connecting" | "open" | "closed";

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
    depthExpiry = null,
    onPriceUpdate,
    onConnectionChange,
    getToken,
  } = options;

  const [prices, setPrices] = useState<Record<string, PriceData>>({});
  const [fundamentals, setFundamentals] = useState<Record<string, FundamentalsData>>({});
  const [depths, setDepths] = useState<Record<string, DepthBook>>({});
  const [tape, setTape] = useState<Record<string, Trade[]>>({});
  const [connected, setConnected] = useState(false);
  const [ibConnected, setIbConnected] = useState(false);
  const [ibIssue, setIbIssue] = useState<string | null>(null);
  const [ibStatusMessage, setIbStatusMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const stalenessTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const lastMessageRef = useRef<number>(Date.now());
  const mountedRef = useRef(true);

  // Connection state machine (ref — not rendered)
  const connStateRef = useRef<ConnState>("idle");
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
  const desiredDepthRef = useRef<string | null>(null);
  const desiredDepthExpiryRef = useRef<string | null>(null);
  const lastSentDepthKeyRef = useRef<string | null>(null);

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

  const normalizedDepthSymbol =
    depthSymbol && depthSymbol.trim().length > 0 ? depthSymbol.trim() : null;
  const normalizedDepthExpiry =
    depthExpiry && depthExpiry.trim().length > 0 ? depthExpiry.trim() : null;

  // Sync refs during render (before any useCallback/useEffect)
  desiredRef.current = {
    symbols: normalizedSymbols,
    contracts: normalizedContracts,
    indexes: normalizedIndexes,
  };
  desiredDepthRef.current = normalizedDepthSymbol;
  desiredDepthExpiryRef.current = normalizedDepthExpiry;
  onPriceUpdateRef.current = onPriceUpdate;
  onConnectionChangeRef.current = onConnectionChange;
  getTokenRef.current = getToken;

  const socketUrl = resolveRealtimeWebSocketUrl();

  // ---------------------------------------------------------------------------
  // Helpers
  // ---------------------------------------------------------------------------

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

  /**
   * Send subscribe-depth / unsubscribe-depth for the single focused symbol.
   * Mirrors `syncSubscriptions` diff discipline but for the scarce depth
   * ticket: on a focus change the old key is unsubscribed and evicted from
   * `depths` before the new key subscribes.
   */
  const syncDepth = useCallback((ws: WebSocket) => {
    if (ws.readyState !== WebSocket.OPEN) return;

    const desired = desiredDepthRef.current;
    const desiredExpiry = desiredDepthExpiryRef.current;
    // Diff on the PAIR so changing ONLY the expiry (same index symbol) still
    // re-sends subscribe-depth and the relay swaps the resolved future.
    const desiredKey = desired ? `${desired}|${desiredExpiry ?? ""}` : null;
    const previousKey = lastSentDepthKeyRef.current;
    if (desiredKey === previousKey) return; // No change

    // The depths/tape maps are keyed by the bare symbol, not the pair. When the
    // expiry changes for the SAME symbol the relay re-uses that key (swap in
    // place), so only evict the previous book when the SYMBOL itself changed.
    const previousSymbol = previousKey ? previousKey.split("|")[0] : null;
    const symbolChanged = previousSymbol !== desired;

    if (previousSymbol && symbolChanged) {
      ws.send(
        JSON.stringify({ action: "unsubscribe-depth", symbol: previousSymbol }),
      );
      setDepths((prev) => {
        if (!(previousSymbol in prev)) return prev;
        const next = { ...prev };
        delete next[previousSymbol];
        return next;
      });
      // The tape rides the same focused depth symbol — evict it together so a
      // focus switch never leaves a stale tape behind.
      setTape((prev) => {
        if (!(previousSymbol in prev)) return prev;
        const next = { ...prev };
        delete next[previousSymbol];
        return next;
      });
    }

    if (desired) {
      // A focused single-leg OPTION subject is keyed by its composite option
      // key (SYMBOL_YYYYMMDD_STRIKE_RIGHT). The relay's option-depth branch
      // needs the STRUCTURED contract fields (expiry/strike/right) to build the
      // OPRA montage — given only the composite string it falls through to a
      // bogus stock contract and emits depth-unavailable, so the panel degrades
      // to the L1 fallback. Decompose the key here so the relay re-derives the
      // SAME key via its own optionKey() and echoes the book under it.
      const optionContract = parseOptionKey(desired);
      let payload: Record<string, unknown>;
      if (optionContract) {
        payload = {
          action: "subscribe-depth",
          symbol: optionContract.symbol,
          expiry: optionContract.expiry,
          strike: optionContract.strike,
          right: optionContract.right,
        };
      } else if (desiredExpiry) {
        // Futures/index subject with a selected expiry: tell the relay to
        // resolve THIS listed future (not the front month) under the same key.
        payload = {
          action: "subscribe-depth",
          symbol: desired,
          instrument: "future",
          expiry: desiredExpiry,
        };
      } else {
        payload = { action: "subscribe-depth", symbol: desired };
      }
      ws.send(JSON.stringify(payload));
    }

    lastSentDepthKeyRef.current = desiredKey;
  }, []);

  // ---------------------------------------------------------------------------
  // scheduleReconnect — ref-based to break circular dep with connect
  // ---------------------------------------------------------------------------
  const scheduleReconnectRef = useRef<() => void>(() => {});

  // ---------------------------------------------------------------------------
  // connect — idempotent, state-machine-guarded
  // ---------------------------------------------------------------------------
  const connect = useCallback(() => {
    if (!enabled) return;
    const { symbols: syms, contracts: cts, indexes: idxs } = desiredRef.current;
    if (syms.length === 0 && cts.length === 0 && idxs.length === 0) return;

    // Idempotent: no-op if already connecting or open
    if (
      connStateRef.current === "connecting" ||
      connStateRef.current === "open"
    ) {
      wsLog("connect-noop", connStateRef.current);
      return;
    }

    clearReconnectTimer();

    const gen = ++socketGenRef.current;
    connStateRef.current = "connecting";
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
        connStateRef.current = "closed";
        setConnected(false);
        const message = err instanceof Error ? err.message : "Realtime auth failed";
        setError(message);
        wsLog("Failed to authenticate realtime socket:", message);
        scheduleReconnectRef.current();
        return;
      }
      if (gen !== socketGenRef.current) return; // stale connect attempt

      const ws = new WebSocket(url);
      wsRef.current = ws;

    ws.onopen = () => {
      if (gen !== socketGenRef.current || !mountedRef.current) return;
      connStateRef.current = "open";
      reconnectStrategyRef.current.reset(); // Reset backoff on success
      lastMessageRef.current = Date.now();
      setConnected(true);
      setError(null);
      onConnectionChangeRef.current?.(true);
      // Force full send on new connection
      lastSentHashRef.current = "";
      syncSubscriptions(ws);
      // Depth resubscribes from scratch on every fresh socket.
      lastSentDepthKeyRef.current = null;
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
            setIbConnected(message.ib_connected);
            setIbIssue(message.ib_issue ?? null);
            setIbStatusMessage(message.ib_status_message ?? null);
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
      connStateRef.current = "closed";
      clearStalenessTimer();
      setConnected(false);
      setIbIssue(null);
      setIbStatusMessage(null);
      onConnectionChangeRef.current?.(false);
      lastSentHashRef.current = ""; // Next connect must full-sync
      lastSentDepthKeyRef.current = null; // Depth re-subscribes on reconnect
      wsLog("close", { gen });
      scheduleReconnectRef.current();
    };

    ws.onerror = () => {
      if (gen !== socketGenRef.current || !mountedRef.current) return;
      connStateRef.current = "closed";
      setConnected(false);
      setError("Connection lost");
      onConnectionChangeRef.current?.(false);
      wsLog("error", { gen });
      ws.close();
    };
    })();
  }, [enabled, socketUrl, clearReconnectTimer, clearStalenessTimer, syncSubscriptions, syncDepth]);

  // Wire scheduleReconnect via ref to avoid circular dep
  const scheduleReconnect = useCallback(() => {
    if (!mountedRef.current || !enabled) return;
    const { symbols: syms, contracts: cts, indexes: idxs } = desiredRef.current;
    if (syms.length === 0 && cts.length === 0 && idxs.length === 0) return;

    const strategy = reconnectStrategyRef.current;
    if (!strategy.canRetry()) {
      setError("Max reconnect attempts reached");
      return;
    }

    const delay = strategy.nextDelay();
    wsLog("reconnect-scheduled", { attempt: strategy.attempt, delay: Math.round(delay) });

    clearReconnectTimer();
    reconnectTimeoutRef.current = setTimeout(() => {
      if (mountedRef.current && enabled) {
        connStateRef.current = "idle"; // Allow connect()
        connect();
      }
    }, delay);
  }, [enabled, clearReconnectTimer, connect]);

  // Keep the ref in sync
  scheduleReconnectRef.current = scheduleReconnect;

  const reconnect = useCallback(() => {
    // Force re-entry into idle so connect() isn't a no-op
    connStateRef.current = "idle";
    reconnectStrategyRef.current.reset();
    connect();
  }, [connect]);

  // ---------------------------------------------------------------------------
  // getSnapshot — isolated WS, unchanged
  // ---------------------------------------------------------------------------
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

        const timeout = setTimeout(() => {
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
                  clearTimeout(timeout);
                  socket.close();
                  resolve(results);
                }
              } else if (message.type === "error") {
                clearTimeout(timeout);
                socket.close();
                reject(new Error(message.message));
              }
            } catch (e) {
              console.error("Failed to parse message:", e);
            }
          };

          socket.onerror = () => {
            clearTimeout(timeout);
            socket.close();
            reject(new Error("Failed to connect to price server"));
          };
        };

        buildAuthenticatedWebSocketUrl(socketUrl, getTokenRef.current)
          .then((url) => {
            ws = new WebSocket(url);
            wireSnapshotSocket(ws);
          })
          .catch((error_) => {
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

  // ---------------------------------------------------------------------------
  // Main lifecycle effect — connect/disconnect based on enabled + subscriptions
  // ---------------------------------------------------------------------------
  useEffect(() => {
    mountedRef.current = true;

    if (enabled && hasSubscriptions) {
      connect();
    } else {
      // Teardown
      clearReconnectTimer();
      clearStalenessTimer();
      if (wsRef.current) {
        if (wsRef.current.readyState === WebSocket.OPEN) {
          wsRef.current.close();
        }
        wsRef.current = null;
      }
      connStateRef.current = "idle";
      lastSentHashRef.current = "";
      lastSentDepthKeyRef.current = null;
      setConnected(false);
      onConnectionChangeRef.current?.(false);
    }

    return () => {
      mountedRef.current = false;
      clearReconnectTimer();
      clearStalenessTimer();
      if (wsRef.current) {
        if (wsRef.current.readyState === WebSocket.OPEN) {
          wsRef.current.close();
        }
        wsRef.current = null;
      }
      connStateRef.current = "idle";
      lastSentHashRef.current = "";
      lastSentDepthKeyRef.current = null;
    };
  }, [enabled, hasSubscriptions, connect, clearReconnectTimer, clearStalenessTimer]);

  // ---------------------------------------------------------------------------
  // Subscription sync effect — sends diffs over open connection
  // ---------------------------------------------------------------------------
  useEffect(() => {
    const ws = wsRef.current;
    if (ws && connStateRef.current === "open") {
      syncSubscriptions(ws);
    }
    // If still connecting, onopen will flush via syncSubscriptions
  }, [symbolHash, contractHash, indexHash, syncSubscriptions]);

  // ---------------------------------------------------------------------------
  // Focused-depth sync effect — diffs the single depth symbol over the open
  // connection. Deliberately does NOT influence `hasSubscriptions`/connect:
  // the focused symbol is already streaming L1, so depth never forces a socket.
  // ---------------------------------------------------------------------------
  useEffect(() => {
    const ws = wsRef.current;
    if (ws && connStateRef.current === "open") {
      syncDepth(ws);
    }
    // If still connecting, onopen will flush via syncDepth
  }, [normalizedDepthSymbol, normalizedDepthExpiry, syncDepth]);

  return {
    prices,
    fundamentals,
    depths,
    tape,
    connected,
    ibConnected,
    ibIssue,
    ibStatusMessage,
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

/**
 * Calculate price change percentage
 */
export function calcChangePercent(
  current: number | null,
  previous: number | null,
): number | null {
  if (current == null || previous == null || previous === 0) return null;
  return ((current - previous) / previous) * 100;
}
