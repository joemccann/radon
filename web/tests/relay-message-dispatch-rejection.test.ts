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
import ts from "typescript";

import { capItems, MAX_SYMBOL_LENGTH } from "../../scripts/lib/relayLimits.js";

const __dirname = resolve(fileURLToPath(import.meta.url), "..");
const source = readFileSync(
  resolve(__dirname, "..", "..", "scripts", "ib_realtime_server.js"),
  "utf8",
);
const relayAst = ts.createSourceFile(
  "ib_realtime_server.js",
  source,
  ts.ScriptTarget.Latest,
  true,
  ts.ScriptKind.JS,
);

function calleeName(expr: ts.Expression): string | null {
  if (ts.isIdentifier(expr)) return expr.text;
  if (ts.isPropertyAccessExpression(expr)) return expr.name.text;
  return null;
}

function callsIn(node: ts.Node): ts.CallExpression[] {
  const found: ts.CallExpression[] = [];
  const visit = (child: ts.Node) => {
    if (ts.isCallExpression(child)) found.push(child);
    ts.forEachChild(child, visit);
  };
  visit(node);
  return found;
}

function topLevelFunction(name: string): ts.FunctionDeclaration {
  const decl = relayAst.statements.find(
    (node): node is ts.FunctionDeclaration =>
      ts.isFunctionDeclaration(node) && node.name?.text === name,
  );
  expect(decl, `${name} exists`).toBeDefined();
  return decl!;
}

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

  it("the message listener calls dispatch and nothing else handles the frame", () => {
    // A comment that still contains the call kept a text search green while
    // the listener invoked handleClientMessage directly. That promise is
    // discarded, and a rejection ends the process for every client.
    const listeners = callsIn(relayAst).filter((call) => {
      const expr = call.expression;
      const event = call.arguments[0];
      return ts.isPropertyAccessExpression(expr)
        && expr.name.text === "on"
        && event !== undefined
        && ts.isStringLiteral(event)
        && event.text === "message";
    });
    expect(listeners).toHaveLength(1);
    const callback = listeners[0].arguments[1];
    expect(callback).toBeDefined();
    const dispatched = callsIn(callback!).filter(
      (call) => calleeName(call.expression) === "dispatchClientMessage",
    );
    const direct = callsIn(callback!).filter(
      (call) => calleeName(call.expression) === "handleClientMessage",
    );
    expect(dispatched).toHaveLength(1);
    expect(direct).toHaveLength(0);

    const owner = topLevelFunction("dispatchClientMessage");
    const handled = callsIn(relayAst).filter(
      (call) => calleeName(call.expression) === "handleClientMessage",
    );
    expect(handled).toHaveLength(1);
    expect(handled[0].getStart(relayAst)).toBeGreaterThan(owner.getStart(relayAst));
    expect(handled[0].getEnd()).toBeLessThan(owner.getEnd());
    expect(ts.isAwaitExpression(handled[0].parent)).toBe(true);
    let cursor: ts.Node | undefined = handled[0];
    while (cursor && !ts.isTryStatement(cursor)) cursor = cursor.parent;
    expect(ts.isTryStatement(cursor)).toBe(true);
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
