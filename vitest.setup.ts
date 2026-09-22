import { afterEach, beforeEach } from "vitest";
import { cleanup } from "@testing-library/react";

type MediaQueryMatcher = (query: string) => boolean;

// Historical suite-wide default: any query containing "dark" matches
// (prefers-color-scheme: dark → true). Hundreds of tests implicitly rely
// on this answer, so it stays the default; use setMatchMedia to override.
const DEFAULT_MATCH_MEDIA_MATCHER: MediaQueryMatcher = (query) => query.includes("dark");

let currentMatchMediaMatcher: MediaQueryMatcher = DEFAULT_MATCH_MEDIA_MATCHER;

/**
 * Override what the global matchMedia shim answers for the CURRENT test.
 * Pass a boolean (every query) or a per-query matcher, BEFORE rendering —
 * consumers read `.matches` at effect time. Resets to the dark-by-default
 * answer before each test. No effect on window.matchMedia stubs a test
 * file installed itself.
 */
export function setMatchMedia(matcher: boolean | MediaQueryMatcher): void {
  currentMatchMediaMatcher = typeof matcher === "function" ? matcher : () => matcher;
}

function resetMatchMediaMatcher(): void {
  currentMatchMediaMatcher = DEFAULT_MATCH_MEDIA_MATCHER;
}

function installMatchMediaShim(): void {
  if (typeof window === "undefined") return;
  if (typeof window.matchMedia === "function") return;

  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    writable: true,
    value: (query: string) => ({
      matches: currentMatchMediaMatcher(query),
      media: query,
      onchange: null,
      addListener() {},
      removeListener() {},
      addEventListener() {},
      removeEventListener() {},
      dispatchEvent() {
        return false;
      },
    }),
  });
}

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
  resetMatchMediaMatcher();
  installLocalStorageShim();
  installMatchMediaShim();
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
