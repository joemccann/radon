import { createRequire } from "node:module";
import { afterEach, beforeEach } from "vitest";

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
// The cleanup must come from the @testing-library/react copy the TEST FILE
// resolves. There are two installs: web/tests/** import
// web/node_modules/@testing-library/react, everything else the root copy, and
// each keeps its own registry of mounted roots. A static import here resolved
// the root copy, so for every web/tests file `cleanup()` unmounted nothing
// (web/tests/vitest-setup-cleanup.test.tsx). Requiring it relative to the test
// file returns the instance the test already loaded (vitest externalizes
// node_modules, so both share Node's module cache), and the call stays
// synchronous.
//
// Registered only when a DOM exists. Most files run in the node environment,
// where nothing renders; loading RTL there (react-dom, @testing-library/dom)
// was pure per-file setup cost on every CI shard
// (web/tests/vitest-setup-node-env.test.ts). The per-call `document` check
// stays for a test that stubs `document` away.
if (typeof document !== "undefined") {
  afterEach(({ task }) => {
    if (typeof document === "undefined") return;
    const rtl: typeof import("@testing-library/react") = createRequire(task.file.filepath)(
      "@testing-library/react",
    );
    rtl.cleanup();
  });
}
