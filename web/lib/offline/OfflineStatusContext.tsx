"use client";

/**
 * OfflineStatusProvider — thin shell around the pure offlineReducer.
 * SSR-safe: initial state is online; navigator/window listeners and the
 * signals bus all attach inside one effect. Consumers read connectivity via
 * useOfflineStatus(); hooks never read this context, they only emit signals.
 */
import { createContext, useContext, useEffect, useRef, useState } from "react";
import {
  INITIAL_OFFLINE_STATE,
  OFFLINE_ENTER_DELAY_MS,
  offlineReducer,
  type OfflineSignal,
  type OfflineState,
} from "./offlineStatus";
import { subscribeOfflineSignals } from "./offlineSignals";

export type OfflineStatus = {
  offline: boolean;
  cachedAt: number | null;
  offlineSince: number | null;
};

const ONLINE_STATUS: OfflineStatus = { offline: false, cachedAt: null, offlineSince: null };

const OfflineStatusContext = createContext<OfflineStatus>(ONLINE_STATUS);

export function useOfflineStatus(): OfflineStatus {
  return useContext(OfflineStatusContext);
}

export function OfflineStatusProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<OfflineState>(INITIAL_OFFLINE_STATE);
  const enterTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    const dispatch = (signal: OfflineSignal) => {
      setState((prev) => offlineReducer(prev, signal, Date.now()));
    };
    const clearEnterTimer = () => {
      if (enterTimerRef.current) {
        clearTimeout(enterTimerRef.current);
        enterTimerRef.current = null;
      }
    };
    const armEnterTimer = () => {
      clearEnterTimer();
      enterTimerRef.current = setTimeout(() => {
        enterTimerRef.current = null;
        dispatch({ type: "enter-delay-elapsed" });
      }, OFFLINE_ENTER_DELAY_MS);
    };
    const onOffline = () => {
      dispatch({ type: "navigator-offline" });
      armEnterTimer();
    };
    const onOnline = () => {
      clearEnterTimer();
      dispatch({ type: "navigator-online" });
    };

    window.addEventListener("offline", onOffline);
    window.addEventListener("online", onOnline);
    const unsubscribe = subscribeOfflineSignals((signal) => {
      if (signal.type === "fetch-success") clearEnterTimer();
      // First offline-served arms the delay; later ones must not reset it
      // (pendingEnterAt keeps the first timestamp in the reducer).
      if (signal.type === "offline-served" && enterTimerRef.current == null) armEnterTimer();
      dispatch(signal);
    });
    if (typeof navigator !== "undefined" && navigator.onLine === false) onOffline();

    return () => {
      window.removeEventListener("offline", onOffline);
      window.removeEventListener("online", onOnline);
      unsubscribe();
      clearEnterTimer();
    };
  }, []);

  const value: OfflineStatus = state.offline
    ? { offline: true, cachedAt: state.cachedAt, offlineSince: state.offlineSince }
    : ONLINE_STATUS;

  return (
    <OfflineStatusContext.Provider value={value}>{children}</OfflineStatusContext.Provider>
  );
}
