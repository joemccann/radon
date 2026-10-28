/**
 * DS-2026-10-28-06: the client-message dispatch discarded the handler promise
 * inside a synchronous try/catch, so any throw reached asynchronously became
 * an unhandled rejection and Node's default policy ended the shared relay.
 * One client's malformed frame was an outage for every connected client.
 *
 * Two controls, both pinned here: the dispatch owns the rejection, and
 * normalizeSymbols accepts only strings so it supplies no such throw.
 */
import { describe, expect, it, vi } from "vitest";
import { runInNewContext } from "node:vm";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { capItems, MAX_SYMBOL_LENGTH } from "../../scripts/lib/relayLimits.js";

const __dirname = resolve(fileURLToPath(import.meta.url), "..");
const source = readFileSync(
  resolve(__dirname, "..", "..", "scripts", "ib_realtime_server.js"),
  "utf8",
);

function fnBody(name: string): string {
  let start = source.indexOf(`function ${name}(`);
  expect(start, `${name} exists`).toBeGreaterThan(-1);
  // Keep an `async` keyword: slicing it off makes the body's `await` a syntax error.
  if (source.slice(start - 6, start) === "async ") start -= 6;
  const next = source.indexOf("\nfunction ", start + 1);
  const nextAsync = source.indexOf("\nasync function ", start + 1);
  const ends = [next, nextAsync].filter((i) => i > -1);
  return source.slice(start, ends.length ? Math.min(...ends) : undefined);
}

describe("relay client-message dispatch", () => {
  it("normalizeSymbols survives a hostile JSON element", () => {
    const normalize = runInNewContext(
      `${fnBody("normalizeSymbols")}; normalizeSymbols`,
      { capItems, MAX_SYMBOL_LENGTH },
    );
    // A plain object whose own toString is null: String() finds toString
    // non-callable and valueOf non-primitive, so ToPrimitive throws.
    const hostile = JSON.parse('[{"toString": null}, "aapl"]');
    expect(() => normalize(hostile)).not.toThrow();
    expect(normalize(hostile)).toEqual(["AAPL"]);
  });

  // Wrapped in tuples: `it.each` spreads a bare array across the parameters.
  it.each([
    [JSON.parse('[{"toString": null}]')],
    [JSON.parse("[1, true, null, {}, []]")],
    [JSON.parse('[{"valueOf": null, "toString": null}]')],
  ])("normalizeSymbols drops non-string element %#", (input) => {
    const normalize = runInNewContext(
      `${fnBody("normalizeSymbols")}; normalizeSymbols`,
      { capItems, MAX_SYMBOL_LENGTH },
    );
    expect(() => normalize(input)).not.toThrow();
    expect(normalize(input)).toEqual([]);
  });

  it("drops a symbol longer than the bound", () => {
    // DS-2026-09-23-09 residual: without a length bound one frame could carry
    // a thousand megabyte-long strings for the relay to uppercase and key.
    const normalize = runInNewContext(
      `${fnBody("normalizeSymbols")}; normalizeSymbols`,
      { capItems, MAX_SYMBOL_LENGTH },
    );
    expect(normalize(["x".repeat(MAX_SYMBOL_LENGTH + 1), "aapl"])).toEqual(["AAPL"]);
    expect(normalize(["x".repeat(MAX_SYMBOL_LENGTH)])).toHaveLength(1);
  });

  // Second rescan: the bound belongs on every subscription path, not just
  // the symbol one. An option composite key and an index exchange are keyed
  // and retained the same way.
  it.each(["normalizeContracts", "normalizeIndexes"])(
    "%s bounds its string fields",
    (name) => {
      const normalize = runInNewContext(
        `${fnBody("boundedField")}; ${fnBody(name)}; ${name}`,
        { capItems, MAX_SYMBOL_LENGTH },
      );
      const long = "x".repeat(MAX_SYMBOL_LENGTH + 1);
      const item =
        name === "normalizeContracts"
          ? { symbol: long, expiry: "20261218", strike: 100, right: "C" }
          : { symbol: long, exchange: "CBOE" };
      expect(normalize([item])).toEqual([]);
      const exchange =
        name === "normalizeIndexes" ? [{ symbol: "VIX", exchange: long }] : [];
      if (exchange.length) expect(normalize(exchange)).toEqual([]);
    },
  );

  it("attaches a rejection handler to the dispatched handler promise", () => {
    // The chokepoint: any future async throw anywhere on the message surface
    // must land here, not in Node's default unhandled-rejection exit.
    expect(source).not.toMatch(/void handleClientMessage\(/);
    expect(source).toMatch(/void dispatchClientMessage\(client, data\);/);
    expect(fnBody("dispatchClientMessage")).toMatch(
      /try \{\s*await handleClientMessage\(client, data\);\s*\} catch/,
    );
  });

  it("the dispatch rejection handler answers the client and keeps the process", () => {
    const dispatch = runInNewContext(
      `${fnBody("dispatchClientMessage")}; dispatchClientMessage`,
      { handleClientMessage, sendMessage, console: { error: vi.fn() } },
    );
    const client = {};
    return dispatch(client, { type: "subscribe" }).then(() => {
      expect(sendMessage).toHaveBeenCalledWith(client, {
        type: "error",
        message: "Request failed",
      });
    });
  });
});

const handleClientMessage = vi.fn(async () => {
  throw new TypeError("Cannot convert object to primitive value");
});
const sendMessage = vi.fn();
