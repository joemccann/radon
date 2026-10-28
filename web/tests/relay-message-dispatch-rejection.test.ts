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

import { capItems } from "../../scripts/lib/relayLimits.js";

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
      { capItems },
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
      { capItems },
    );
    expect(() => normalize(input)).not.toThrow();
    expect(normalize(input)).toEqual([]);
  });

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
