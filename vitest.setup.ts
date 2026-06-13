import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

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
