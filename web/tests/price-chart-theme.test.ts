/**
 * TDD: PriceChart tooltip/crosshair theme awareness
 *
 * The Liveline chart library renders its crosshair/scrub tooltip and badge
 * entirely on canvas using its `theme` prop ('light' | 'dark'). When that prop
 * is hardcoded to "dark", the overlay always renders dark-mode colors even
 * when the UI is in light mode.
 *
 * Fix: PriceChart must accept a `theme` prop and forward it to Liveline.
 *
 * These tests run in the node environment (no jsdom / React rendering).
 * They assert structural guarantees on the source file that can be verified
 * without a DOM.
 */

import { readFileSync } from "fs";
import { resolve } from "path";
import { test, expect } from "vitest";

const src = readFileSync(
  resolve(__dirname, "../components/PriceChart.tsx"),
  "utf-8",
);

// ─── RED: these fail before the fix ───────────────────────────────────────────

test("[theme] PriceChart accepts a `theme` prop in its interface", () => {
  // The interface/props type must declare a `theme` field
  expect(src).toMatch(/theme\s*\??\s*:/);
});

test("[theme] PriceChart does NOT hardcode theme='dark' on <Liveline>", () => {
  // After the fix, the Liveline prop should use the variable, not a literal string
  // We look for `theme={` (dynamic) and reject `theme="dark"` (literal)
  expect(src).not.toMatch(/theme="dark"/);
});

test("[theme] Liveline receives theme from props, not a string literal", () => {
  // The Liveline JSX must use a dynamic expression for theme, e.g. theme={theme}
  // We check that `theme={` appears (dynamic prop binding)
  expect(src).toMatch(/theme=\{/);
});

test("[theme] PriceChart defaults theme to 'dark' when not provided", () => {
  // The default value for the theme prop must preserve dark mode for existing callers.
  // This can be expressed as a default parameter OR a fallback expression.
  const hasPropDefault = /theme\s*=\s*["']dark["']/.test(src); // = "dark" in destructure
  const hasFallbackExpr = /theme\s*\?\?\s*["']dark["']/.test(src); // ?? "dark"
  expect(hasPropDefault || hasFallbackExpr).toBe(true);
});
