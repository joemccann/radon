"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  type WSMessage,
  type PriceData,
  type FundamentalsData,
  type OptionContract,
  type IndexContract,
  normalizeSymbolList,
  symbolKey,
  contractsKey,
  optionKey,
} from "./pricesProtocol";
import { createReconnectStrategy, type ReconnectState } from "./reconnectStrategy";

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
  /** Callback when a price updates */
  onPriceUpdate?: (update: PriceUpdate) => void;
  /** Callback when connection status changes */
  onConnectionChange?: (connected: boolean) => void;
};

export type UsePricesReturn = {
  /** Current prices keyed by symbol */
  prices: Record<string, PriceData>;
  /** Fundamentals data keyed by symbol (from IB generic tick 258) */
  fundamentals: Record<string, FundamentalsData>;
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
    onPriceUpdate,
    onConnectionChange,
  } = options;

  const [prices, setPrices] = useState<Record<string, PriceData>>({});
  const [fundamentals, setFundamentals] = useState<Record<string, FundamentalsData>>({});
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

  // Callback refs (avoid stale closures in WS handlers)
  const onPriceUpdateRef = useRef(onPriceUpdate);
  const onConnectionChangeRef = useRef(onConnectionChange);

  // Stable hashes for change detection
  const symbolHash = symbolKey(symbols);
  const contractHash = contractsKey(contracts);
  const indexHash = useMemo(
    () => indexes.map((i) => `${i.symbol}@${i.exchange}`).sort().join(","),
    [indexes],
  );
  const normalizedSymbols = useMemo(
    () => normalizeSymbolList(symbols),
    [symbolHash],
  );
  const normalizedContracts = useMemo(
    () => contracts,
    [contractHash],
  );
  const normalizedIndexes = useMemo(
    () => indexes,
    [indexHash],
  );

  const hasSubscriptions =
    normalizedSymbols.length > 0 ||
    normalizedContracts.length > 0 ||
    normalizedIndexes.length > 0;

  // Sync refs during render (before any useCallback/useEffect)
  desiredRef.current = {
    symbols: normalizedSymbols,
    contracts: normalizedContracts,
    indexes: normalizedIndexes,
  };
  onPriceUpdateRef.current = onPriceUpdate;
  onConnectionChangeRef.current = onConnectionChange;

  const socketUrl =
    process.env.NEXT_PUBLIC_IB_REALTIME_WS_URL ??
    process.env.IB_REALTIME_WS_URL ??
    "ws://localhost:8765";

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

    const ws = new WebSocket(socketUrl);
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
  }, [enabled, socketUrl, clearReconnectTimer, clearStalenessTimer, syncSubscriptions]);

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
        const ws = new WebSocket(socketUrl);
        const results: Record<string, PriceData> = {};
        const pending = new Set(symbolsToRequest);

        const timeout = setTimeout(() => {
          ws.close();
          resolve(results);
        }, 5000);

        ws.onopen = () => {
          ws.send(
            JSON.stringify({ action: "snapshot", symbols: symbolsToRequest }),
          );
        };

        ws.onmessage = (event) => {
          try {
            const message = JSON.parse(event.data as string) as WSMessage;
            if (message.type === "snapshot") {
              const symbol = message.data.symbol.toUpperCase();
              results[symbol] = message.data;
              pending.delete(symbol);

              if (pending.size === 0) {
                clearTimeout(timeout);
                ws.close();
                resolve(results);
              }
            } else if (message.type === "error") {
              clearTimeout(timeout);
              ws.close();
              reject(new Error(message.message));
            }
          } catch (e) {
            console.error("Failed to parse message:", e);
          }
        };

        ws.onerror = () => {
          clearTimeout(timeout);
          ws.close();
          reject(new Error("Failed to connect to price server"));
        };
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

  return {
    prices,
    fundamentals,
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
