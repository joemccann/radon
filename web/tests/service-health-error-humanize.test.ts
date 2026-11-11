/**
 * @vitest-environment node
 *
 * Tests for ``humanizeServiceHealthError`` - the upgraded formatter
 * that turns raw ``service_health.last_error`` payloads into short,
 * banner-ready prose for every error shape we ship.
 *
 * The previous formatter (``formatServiceHealthError``) shipped clean
 * text but kept the developer-flavoured wording verbatim. The banner
 * read like a stack trace excerpt:
 *
 *   "ERR: cash flow fetch failed: Flex SendRequest failed (code 1001):
 *    Statement could not be generated at this time. Please try again
 *    shortly."
 *
 * The humanizer recognises the known error shapes (Flex throttle, Flex
 * auth, timeout, WAL conflict, network blip, subprocess returncode)
 * and rewrites them in plain language with a ``retry in <window>``
 * suffix when ``next_attempt_at`` is set. Anything we don't recognise
 * passes through the existing sanitiser unchanged so we never regress
 * on novel error shapes.
 */
import { describe, it, expect, vi, afterEach } from "vitest";
import {
  humanizeServiceHealthError,
} from "../lib/serviceHealthError";

afterEach(() => {
  vi.useRealTimers();
});

const FIXED_NOW = new Date("2026-05-10T20:00:00Z").getTime();

function freezeNow(): void {
  vi.useFakeTimers();
  vi.setSystemTime(new Date(FIXED_NOW));
}

describe("humanizeServiceHealthError - Flex throttle codes", () => {
  it("rewrites a 1001 transient generation failure truthfully, not as a rate limit", () => {
    freezeNow();
    const raw = JSON.stringify({
      message:
        "ERR: cash flow fetch failed: Flex SendRequest failed (code 1001): Statement could not be generated at this time. Please try again shortly.",
    });
    const out = humanizeServiceHealthError(raw);
    // 1018 is the only Flex rate limit (scripts/cash_flow_sync.py
    // _FLEX_THROTTLE_CODES); 1001 is a transient generation failure.
    expect(out).toBe("Flex statement could not be generated (transient)");
    expect(out.toLowerCase()).not.toContain("rate limit");
    // No raw stack-trace flavor should leak into banner copy.
    expect(out).not.toContain("ERR:");
    expect(out).not.toContain("SendRequest");
    expect(out).not.toContain("(code 1001)");
  });

  it("rewrites a 1018 Flex throttle the same way", () => {
    const raw = JSON.stringify({
      message:
        "Flex SendRequest failed (code 1018): Too many requests have been made from this token. Please try again shortly.",
    });
    const out = humanizeServiceHealthError(raw);
    expect(out.toLowerCase()).toContain("flex");
    expect(out.toLowerCase()).toContain("rate limit");
    expect(out).not.toContain("(code 1018)");
  });

  it("rewrites a 1019 Flex throttle the same way", () => {
    const raw = JSON.stringify({
      message:
        "Flex SendRequest failed (code 1019): Statement generation in progress. Please try again shortly.",
    });
    const out = humanizeServiceHealthError(raw);
    expect(out.toLowerCase()).toContain("flex");
    // 1019 is a soft "still processing" — the user-facing copy should
    // be calm, not alarming.
    expect(out.toLowerCase()).toMatch(/rate limit|still processing|busy/);
  });

  it("appends a relative retry window when next_attempt_at is in the future", () => {
    freezeNow();
    const raw = JSON.stringify({
      message: "Flex SendRequest failed (code 1001): Statement could not be generated...",
      next_attempt_at: new Date(FIXED_NOW + 23 * 60 * 60 * 1000).toISOString(),
    });
    const out = humanizeServiceHealthError(raw);
    expect(out.toLowerCase()).toContain("retry");
    // 23h should round to "in 23h" or "in about 1 day" — not raw ISO.
    expect(out).not.toMatch(/2026-/);
    expect(out).not.toContain("T20");
  });

  it("formats next_attempt_at < 1h as minutes", () => {
    freezeNow();
    const raw = JSON.stringify({
      message: "Flex SendRequest failed (code 1001): ...",
      next_attempt_at: new Date(FIXED_NOW + 30 * 60 * 1000).toISOString(),
    });
    const out = humanizeServiceHealthError(raw);
    expect(out.toLowerCase()).toMatch(/30\s*m|30 min/);
  });

  it("formats next_attempt_at >= 1 day as days", () => {
    freezeNow();
    const raw = JSON.stringify({
      message: "Flex SendRequest failed (code 1001): ...",
      next_attempt_at: new Date(FIXED_NOW + 48 * 60 * 60 * 1000).toISOString(),
    });
    const out = humanizeServiceHealthError(raw);
    expect(out.toLowerCase()).toMatch(/2\s*d|2 day/);
  });

  it("does not append retry copy when next_attempt_at is in the past", () => {
    freezeNow();
    const raw = JSON.stringify({
      message: "Flex SendRequest failed (code 1001): ...",
      next_attempt_at: new Date(FIXED_NOW - 60 * 1000).toISOString(),
    });
    const out = humanizeServiceHealthError(raw);
    expect(out.toLowerCase()).not.toContain("retry");
  });
});

describe("humanizeServiceHealthError - Flex auth & permission", () => {
  it("rewrites a 1012 Flex auth failure", () => {
    const raw = JSON.stringify({
      message: "Flex SendRequest failed (code 1012): Token has expired",
    });
    const out = humanizeServiceHealthError(raw);
    expect(out.toLowerCase()).toContain("flex");
    expect(out.toLowerCase()).toMatch(/token|auth|credential/);
    expect(out.toLowerCase()).toContain("expired");
    // Operator-actionable - tells the user this needs human action,
    // not "wait it out".
    expect(out).not.toContain("(code 1012)");
  });
});

describe("humanizeServiceHealthError - timeouts & subprocess", () => {
  it("rewrites a subprocess timeout", () => {
    const raw = JSON.stringify({
      message: "cash_flow_sync timed out after 180s",
    });
    const out = humanizeServiceHealthError(raw);
    expect(out.toLowerCase()).toContain("timed out");
    // Generic subject - no internal script name.
    expect(out).not.toContain("cash_flow_sync");
  });

  it("rewrites a subprocess returncode failure", () => {
    const raw = JSON.stringify({
      message: "Traceback (most recent call last) | File some.py | RuntimeError: bad thing",
    });
    const out = humanizeServiceHealthError(raw);
    // Should NOT show the raw traceback.
    expect(out.toLowerCase()).not.toContain("traceback");
    expect(out.toLowerCase()).not.toContain("file some.py");
  });
});

describe("humanizeServiceHealthError - network & DB", () => {
  it("rewrites a connection-refused as a network blip", () => {
    const raw = JSON.stringify({ message: "connection refused" });
    const out = humanizeServiceHealthError(raw);
    expect(out.toLowerCase()).toMatch(/network|connection|unreachable/);
  });

  it("rewrites a urllib URLError", () => {
    const raw = JSON.stringify({
      message: "<urlopen error [Errno 60] Operation timed out>",
    });
    const out = humanizeServiceHealthError(raw);
    expect(out.toLowerCase()).toMatch(/network|connection|timed out|unreachable/);
    expect(out).not.toContain("urlopen");
    expect(out).not.toContain("Errno");
  });

  it("rewrites a WAL conflict", () => {
    const raw = JSON.stringify({ message: "WAL conflict on replica" });
    const out = humanizeServiceHealthError(raw);
    expect(out.toLowerCase()).toMatch(/database|sync|locked|busy/);
    expect(out).not.toContain("WAL");
  });
});

describe("humanizeServiceHealthError - graceful fallback", () => {
  it("passes through novel error shapes via the existing sanitiser", () => {
    const raw = JSON.stringify({ message: "something we have never seen before" });
    const out = humanizeServiceHealthError(raw);
    expect(out).toContain("something we have never seen before");
    // Still no JSON structural characters.
    expect(out).not.toContain("{");
    expect(out).not.toContain('"');
  });

  it("returns the fallback for null / empty", () => {
    expect(humanizeServiceHealthError(null)).toBe("service unavailable");
    expect(humanizeServiceHealthError(undefined)).toBe("service unavailable");
    expect(humanizeServiceHealthError("")).toBe("service unavailable");
    expect(humanizeServiceHealthError({})).toBe("service unavailable");
  });

  it("accepts an already-parsed object", () => {
    const out = humanizeServiceHealthError({
      message: "Flex SendRequest failed (code 1001): ...",
    });
    expect(out).toBe("Flex statement could not be generated (transient)");
  });

  it("strips raw script prefixes like ERR: even from unrecognised messages", () => {
    const raw = JSON.stringify({ message: "ERR: something went sideways" });
    const out = humanizeServiceHealthError(raw);
    expect(out).not.toContain("ERR:");
    expect(out.toLowerCase()).toContain("something went sideways");
  });

  it("respects a maxLength parameter for the final string", () => {
    const long =
      "Flex SendRequest failed (code 1001): Statement could not be generated at this time and we have a lot more text to add to push past any limit boundary";
    const raw = JSON.stringify({ message: long });
    const out = humanizeServiceHealthError(raw, { maxLength: 60 });
    expect(out.length).toBeLessThanOrEqual(60);
  });
});

describe("humanizeServiceHealthError - format preserves base contract", () => {
  it("never leaks JSON structure even with malformed payload", () => {
    const raw = '{"message": "broken'; // truncated, unparseable
    const out = humanizeServiceHealthError(raw);
    expect(out).not.toContain("{");
    expect(out).not.toContain('"');
  });

  it("collapses whitespace so multi-line errors render as one line", () => {
    const raw = JSON.stringify({
      message: "first line\n\nsecond line\tthird",
    });
    const out = humanizeServiceHealthError(raw);
    expect(out).not.toContain("\n");
    expect(out).not.toContain("\t");
  });
});
