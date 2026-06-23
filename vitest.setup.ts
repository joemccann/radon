import { afterEach, beforeEach } from "vitest";
import { cleanup } from "@testing-library/react";

function installLocalStorageShim(): void {
  if (typeof window === "undefined") return;

  const current = window.localStorage;
  if (
    current
    && typeof current.clear === "function"
    && typeof current.getItem === "function"
    && typeof current.setItem === "function"
    && typeof current.removeItem === "function"
  ) {
    return;
  }

  const store = new Map<string, string>();
  const storage: Storage = {
    get length() {
      return store.size;
    },
    clear() {
      store.clear();
    },
    getItem(key: string) {
      return store.has(key) ? store.get(key)! : null;
    },
    key(index: number) {
      return Array.from(store.keys())[index] ?? null;
    },
    removeItem(key: string) {
      store.delete(key);
    },
    setItem(key: string, value: string) {
      store.set(key, String(value));
    },
  };

  Object.defineProperty(window, "localStorage", {
    value: storage,
    configurable: true,
  });
}

beforeEach(() => {
  installLocalStorageShim();
});

// Global test isolation: unmount any @testing-library-rendered React tree after
// each test. Without this there is no auto-cleanup, so jsdom components (and
// their effects, timers, and WebSocket onmessage handlers) leak into the NEXT
// test — a leaked `usePrices` render fired after jsdom teardown and threw
// "window is not defined", and a leaked MockWebSocket injected a stale message
// that corrupted regime-llm-card. Both only surfaced under `--coverage` because
// instrumentation's slower timing let the leaked async escape.
//
// Guarded on `document` so it is a no-op in the node-env (default) tests, where
// nothing renders and `cleanup()` would otherwise fail on a missing document.
afterEach(() => {
  if (typeof document !== "undefined") {
    cleanup();
  }
});
