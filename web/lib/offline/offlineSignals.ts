/**
 * Module-level pub/sub so data hooks can report fetch outcomes without
 * threading React context. The OfflineStatusProvider is the only subscriber
 * in production; hooks only ever emit.
 */
import type { OfflineSignal } from "./offlineStatus";

export type BusSignal = Extract<
  OfflineSignal,
  { type: "fetch-failure" } | { type: "fetch-success" } | { type: "offline-served" }
>;

type Listener = (signal: BusSignal) => void;

const listeners = new Set<Listener>();

export function subscribeOfflineSignals(listener: Listener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

function emit(signal: BusSignal): void {
  for (const listener of listeners) listener(signal);
}

export function reportFetchFailure(): void {
  emit({ type: "fetch-failure" });
}

export function reportFetchSuccess(): void {
  emit({ type: "fetch-success" });
}

export function reportOfflineServed(cachedAt: number | null): void {
  emit({ type: "offline-served", cachedAt });
}
