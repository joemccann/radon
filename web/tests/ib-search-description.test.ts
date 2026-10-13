/**
 * T-551: symbol search must put the IB company name on the wire.
 *
 * TickerSearch renders `description` only when the relay includes it.
 * `web/tests/ib-index-stream-contracts.test.ts` greps the symbolSamples
 * handler and never names that field, and the row tests inject the name
 * themselves. Deleting the spread in `ib_realtime_server.js` left both green.
 */
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
import { describe, expect, it } from "vitest";
import ts from "typescript";

const source = readFileSync(new URL("../../scripts/ib_realtime_server.js", import.meta.url), "utf8");

function searchRelay() {
  const ast = ts.createSourceFile("relay.js", source, ts.ScriptTarget.Latest, true, ts.ScriptKind.JS);
  const fn = ast.statements.find((node) =>
    ts.isFunctionDeclaration(node) && node.name?.text === "wireIBEvents");
  expect(fn).toBeTruthy();
  const callbacks = new Map<string, (...args: unknown[]) => void>();
  const searchRequestClients = new Map<number, { client: object; pattern: string }>();
  const searchCache = new Map<string, { results: unknown[]; ts: number }>();
  const sent: unknown[] = [];
  const client = {};
  const context = {
    ib: { on: (name: string, callback: (...args: unknown[]) => void) => callbacks.set(name, callback) },
    EventName: new Proxy({}, { get: (_target, key) => key }),
    ibClientGeneration: 1,
    searchRequestClients,
    searchCache,
    SEARCH_CACHE_MAX: 200,
    DEPTH_ENABLED: false,
    sendMessage: (_client: object, message: unknown) => sent.push(message),
    verbose: () => {},
    Date,
    console,
  };
  runInNewContext(`${fn!.getText(ast)}\nwireIBEvents();`, context);
  return {
    emit(reqId: number, contracts: unknown[]) {
      callbacks.get("symbolSamples")!(reqId, contracts);
    },
    sent,
    client,
    searchRequestClients,
    searchCache,
  };
}

describe("relay symbol search company names", () => {
  it("forwards a nested description and omits a blank or missing one", () => {
    const relay = searchRelay();
    relay.searchRequestClients.set(7, { client: relay.client, pattern: "NV" });
    relay.emit(7, [
      {
        derivativeSecTypes: ["OPT"],
        contract: {
          conId: 2, symbol: "NVDA", secType: "STK", primaryExch: "NASDAQ",
          currency: "USD", description: "NVIDIA CORP",
        },
      },
      {
        derivativeSecTypes: [],
        contract: {
          conId: 4, symbol: "NVO", secType: "STK", primaryExch: "NYSE",
          currency: "USD", description: "",
        },
      },
      {
        contract: {
          conId: 9, symbol: "ES", secType: "FUT", primaryExch: "CME", currency: "USD",
        },
      },
    ]);
    expect(relay.sent).toEqual([{
      type: "searchResults",
      pattern: "NV",
      results: [
        {
          conId: 2, symbol: "NVDA", secType: "STK", primaryExchange: "NASDAQ",
          currency: "USD", derivativeSecTypes: ["OPT"], description: "NVIDIA CORP",
        },
        {
          conId: 4, symbol: "NVO", secType: "STK", primaryExchange: "NYSE",
          currency: "USD", derivativeSecTypes: [],
        },
        {
          conId: 9, symbol: "ES", secType: "FUT", primaryExchange: "CME",
          currency: "USD", derivativeSecTypes: [],
        },
      ],
    }]);
    expect(relay.searchCache.get("NV")?.results).toEqual(
      (relay.sent[0] as { results: unknown[] }).results,
    );
    expect(relay.searchRequestClients.has(7)).toBe(false);
  });

  it("reads a legacy flat sample the same way, including its description", () => {
    const relay = searchRelay();
    relay.searchRequestClients.set(3, { client: relay.client, pattern: "AAPL" });
    relay.emit(3, [{
      conId: 265598, symbol: "AAPL", secType: "STK", primaryExchange: "NASDAQ",
      currency: "USD", description: "APPLE INC", derivativeSecTypes: ["OPT", "WAR"],
    }]);
    expect(relay.sent[0]).toMatchObject({
      type: "searchResults",
      results: [{
        conId: 265598, symbol: "AAPL", secType: "STK", primaryExchange: "NASDAQ",
        currency: "USD", derivativeSecTypes: ["OPT", "WAR"], description: "APPLE INC",
      }],
    });
  });
});
