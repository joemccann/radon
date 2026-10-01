import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
import { describe, expect, it, vi } from "vitest";
import ts from "typescript";

const source = readFileSync(new URL("../../scripts/ib_realtime_server.js", import.meta.url), "utf8");
const { applyDepthOp } = await import(new URL("../../scripts/lib/depthLadder.js", import.meta.url).pathname);
const { planDepthAdmission } = await import(new URL("../../scripts/lib/depthBudget.js", import.meta.url).pathname);

function relayFixture({ enabled = true, connected = true } = {}) {
  // Evaluate actual relay declarations without the entry point's socket startup.
  const ast = ts.createSourceFile("relay.js", source, ts.ScriptTarget.Latest, true, ts.ScriptKind.JS);
  const functions = new Set([
    "wireIBEvents", "startDepthSubscription", "stopDepthSubscription", "collectActiveDepthTickets",
    "applyDepthDelta", "hydrateAndBroadcastDepth", "serializeLadder", "nbboPriceForOptionLadder",
    "summarizeOptionNbbo", "depthFeedLabel", "emitDepthUnavailable",
  ]);
  const constants = new Set(["DEPTH_ENABLED", "MAX_CONCURRENT_DEPTH", "DEPTH_NUM_ROWS_EQUITY", "DEPTH_NUM_ROWS_FUTURES"]);
  const declarations = ast.statements.filter(node =>
    (ts.isFunctionDeclaration(node) && node.name && functions.has(node.name.text))
    || (ts.isVariableStatement(node) && node.declarationList.declarations.some(declaration =>
      ts.isIdentifier(declaration.name) && constants.has(declaration.name.text))),
  );
  expect(declarations).toHaveLength(functions.size + constants.size);
  const callbacks = new Map<string, (...args: unknown[]) => void>();
  const reqMktDepth = vi.fn();
  const bufferDepthForClient = vi.fn();
  const symbolDepthStates = new Map();
  const depthSubscribers = new Map();
  const context = {
    process: { env: { RADON_DEPTH_ENABLED: enabled ? "1" : "0" } },
    ib: { on: (name: string, callback: (...args: unknown[]) => void) => callbacks.set(name, callback), reqMktDepth, cancelMktDepth: vi.fn() },
    EventName: new Proxy({}, { get: (_target, key) => key }),
    ibConnected: connected, ibClientGeneration: 1, nextRequestId: 0,
    symbolDepthStates, depthSubscribers, depthRequestIdToSymbol: new Map(),
    applyDepthOp, planDepthAdmission, bufferDepthForClient,
    markTick: vi.fn(), verbose: vi.fn(), sendMessage: vi.fn(),
    nowIso: () => "2026-10-01T15:00:00Z", console,
  };
  const start = runInNewContext(`${declarations.map(node => node.getText(ast)).join("\n")}\nwireIBEvents();\nstartDepthSubscription`, context);
  const client = {};
  function subscribe(isFutures = false) {
    const symbol = isFutures ? "ES" : "AAPL";
    const contract = { symbol, secType: isFutures ? "FUT" : "STK", exchange: isFutures ? "CME" : "SMART" };
    depthSubscribers.set(symbol, new Set([client]));
    start(symbol, contract, { kind: isFutures ? "future" : "stock", isFutures, requestingClient: client });
    return { symbol, contract };
  }
  function insert(id: number, position: number, isFutures: boolean, side = 1) {
    const event = isFutures ? "updateMktDepth" : "updateMktDepthL2";
    const args = isFutures
      ? [id, position, 0, side, 100 - position, position + 1]
      : [id, position, "ARCA", 0, side, 100 - position, position + 1, true];
    callbacks.get(event)!(...args);
  }
  return { subscribe, insert, callbacks, client, reqMktDepth, bufferDepthForClient, symbolDepthStates };
}

describe("relay depth budget at the broker and subscriber boundaries", () => {
  it.each([[false, 40, true], [true, 10, false]] as const)(
    "requests and delivers the complete budget for futures=%s",
    (isFutures, rows, smart) => {
      const relay = relayFixture();
      const { symbol, contract } = relay.subscribe(isFutures);
      expect(relay.reqMktDepth).toHaveBeenCalledExactlyOnceWith(1, contract, rows, smart);
      for (let position = 0; position < rows; position++) relay.insert(1, position, isFutures);
      const book = relay.bufferDepthForClient.mock.calls.at(-1)![2];
      expect(relay.bufferDepthForClient.mock.calls.at(-1)!.slice(0, 2)).toEqual([relay.client, symbol]);
      expect(book.bid).toHaveLength(rows);
      expect(book.ask).toEqual([]);
      expect(book.bid[rows - 1]).toMatchObject({ price: 101 - rows, size: rows, exchange: isFutures ? null : "ARCA" });
      expect(book.isSmartDepth).toBe(smart);
      expect(book.entitled).toBe(true);
      // One more insert must not grow the ladder beyond the broker request.
      relay.insert(1, rows, isFutures);
      expect(relay.bufferDepthForClient.mock.calls.at(-1)![2].bid).toHaveLength(rows);
      // The previously delivered snapshot must survive later changes.
      relay.insert(1, 0, isFutures);
      expect(book.bid[rows - 1].size).toBe(rows);
      relay.insert(1, 0, isFutures, 0);
      expect(relay.bufferDepthForClient.mock.calls.at(-1)![2].ask).toHaveLength(1);
    },
  );

  it.each([{ enabled: false }, { connected: false }])("opens no ticket behind a closed gate: %j", settings => {
    const relay = relayFixture(settings);
    relay.subscribe();
    expect(relay.reqMktDepth).not.toHaveBeenCalled();
    expect(relay.symbolDepthStates.size).toBe(0);
    if (settings.enabled === false) expect(relay.callbacks.has("updateMktDepthL2")).toBe(false);
  });

  it("ignores another request's events and does not duplicate a subscription", () => {
    const relay = relayFixture();
    relay.subscribe();
    relay.subscribe();
    expect(relay.reqMktDepth).toHaveBeenCalledTimes(1);
    relay.insert(999, 0, false);
    expect(relay.bufferDepthForClient).not.toHaveBeenCalled();
  });
});
