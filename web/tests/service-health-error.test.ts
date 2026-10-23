/**
 * @vitest-environment node
 *
 * Tests for ``formatServiceHealthError`` — the normalization helper that
 * turns raw ``service_health.last_error`` payloads into clean, plain-
 * text strings safe for direct banner rendering.
 *
 * The helper protects users from the historical bug where a
 * JSON-stringified error dict leaked ``{``, ``"``, and ``}`` characters
 * into the dashboard banner copy.
 */
import { describe, it, expect } from "vitest";
import { formatServiceHealthError } from "../lib/serviceHealthError";

describe("formatServiceHealthError", () => {
  it("extracts message from a JSON-stringified object", () => {
    const raw = JSON.stringify({ message: "ERR: cash flow fetch failed" });
    const formatted = formatServiceHealthError(raw);
    expect(formatted).toBe("ERR: cash flow fetch failed");
    expect(formatted).not.toContain("{");
    expect(formatted).not.toContain("}");
    expect(formatted).not.toContain('"');
  });

  it("strips raw JSON structure even from the production payload that triggered this fix", () => {
    const raw = JSON.stringify({
      message:
        "ERR: cash flow fetch failed: Flex SendRequest failed (code 1001): Statement could not be generated at this time. Please try again shortly.",
    });
    const formatted = formatServiceHealthError(raw);
    expect(formatted).not.toContain("{");
    expect(formatted).not.toContain("}");
    expect(formatted).not.toContain('"');
    expect(formatted.startsWith("ERR: cash flow fetch failed")).toBe(true);
  });

  it("passes through a plain non-JSON string unchanged when short enough", () => {
    expect(formatServiceHealthError("WAL locked")).toBe("WAL locked");
  });

  it("prefers `error` when `message` is absent", () => {
    const raw = JSON.stringify({ error: "connection refused", wal_conflicts_observed: 3 });
    expect(formatServiceHealthError(raw)).toBe("connection refused");
  });

  it("prefers `detail` when neither `message` nor `error` is present", () => {
    const raw = JSON.stringify({ detail: "Flex token expired" });
    expect(formatServiceHealthError(raw)).toBe("Flex token expired");
  });

  it("prefers `reason` as the last conventional key", () => {
    const raw = JSON.stringify({ reason: "rate limited" });
    expect(formatServiceHealthError(raw)).toBe("rate limited");
  });

  it("falls through to `summary` when no other conventional key is present", () => {
    const raw = JSON.stringify({
      summary: "config drift: file-mismatch:caddyfile",
      drift_count: 1,
      drifts: [{ id: "file-mismatch:caddyfile", detail: "live vs repo: +0/-10 lines" }],
    });
    expect(formatServiceHealthError(raw)).toBe("config drift: file-mismatch:caddyfile");
  });

  it("respects key precedence: message > error > detail > reason", () => {
    const raw = JSON.stringify({
      reason: "fourth",
      detail: "third",
      error: "second",
      message: "first",
    });
    expect(formatServiceHealthError(raw)).toBe("first");
  });

  it("accepts an already-parsed object as input", () => {
    expect(formatServiceHealthError({ message: "boom" })).toBe("boom");
  });

  it("falls back to generic copy for null", () => {
    expect(formatServiceHealthError(null)).toBe("service unavailable");
  });

  it("falls back to generic copy for undefined", () => {
    expect(formatServiceHealthError(undefined)).toBe("service unavailable");
  });

  it("falls back to generic copy for an empty object", () => {
    expect(formatServiceHealthError({})).toBe("service unavailable");
  });

  it("falls back to generic copy for an object with only structural fields", () => {
    expect(
      formatServiceHealthError({ wal_conflicts_observed: 3, retry_count: 5 }),
    ).toBe("service unavailable");
  });

  it("falls back to generic copy for an empty string", () => {
    expect(formatServiceHealthError("")).toBe("service unavailable");
  });

  it("truncates long messages cleanly at a word boundary with an ellipsis", () => {
    const long =
      "ERR: cash flow fetch failed: Flex SendRequest failed (code 1001): Statement could not be generated at this time. Please try again shortly.";
    const formatted = formatServiceHealthError(long, { maxLength: 80 });
    expect(formatted.length).toBeLessThanOrEqual(80);
    expect(formatted.endsWith("...")).toBe(true);
    // Word boundary respected — does not end mid-word like "stat..."
    const beforeEllipsis = formatted.slice(0, -3).trimEnd();
    expect(beforeEllipsis.endsWith(" ")).toBe(false);
    // The trailing visible char should be a real word character followed
    // by the ellipsis, not a partial token.
    expect(/[a-zA-Z0-9):.,!?-]$/.test(beforeEllipsis)).toBe(true);
  });

  it("returns the message intact when shorter than maxLength", () => {
    expect(formatServiceHealthError("short and sweet", { maxLength: 80 })).toBe(
      "short and sweet",
    );
  });

  it("survives a malformed JSON-looking string by stripping braces", () => {
    const raw = '{"message": "broken'; // truncated, unparseable
    const formatted = formatServiceHealthError(raw);
    expect(formatted).not.toContain("{");
    expect(formatted).not.toContain('"');
    expect(formatted.length).toBeGreaterThan(0);
  });

  it("collapses whitespace so multi-line errors render as one line", () => {
    expect(formatServiceHealthError("line one\n\nline two\tline three")).toBe(
      "line one line two line three",
    );
  });
});
