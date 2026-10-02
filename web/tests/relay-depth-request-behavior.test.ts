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
    "unsubscribeClientFromDepth", "admitDepthAwaitingBudget", "restoreDepthSubscriptions",
    "startTapeSubscription", "stopTapeSubscription",
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
  const reqTickByTickData = vi.fn();
  const bufferDepthForClient = vi.fn();
  const symbolDepthStates = new Map();
  const depthSubscribers = new Map();
  const context = {
    process: { env: { RADON_DEPTH_ENABLED: enabled ? "1" : "0" } },
    ib: { on: (name: string, callback: (...args: unknown[]) => void) => callbacks.set(name, callback), reqMktDepth, cancelMktDepth: vi.fn(), reqTickByTickData, cancelTickByTickData: vi.fn() },
    EventName: new Proxy({}, { get: (_target, key) => key }),
    ibConnected: connected, ibClientGeneration: 1, nextRequestId: 0,
    symbolDepthStates, depthSubscribers, depthRequestIdToSymbol: new Map(),
    symbolTapeStates: new Map(), tapeRequestIdToSymbol: new Map(), TickByTickDataType: { AllLast: "AllLast" },
    applyDepthOp, planDepthAdmission, bufferDepthForClient,
    markTick: vi.fn(), verbose: vi.fn(), sendMessage: vi.fn(),
    nowIso: () => "2026-10-01T15:00:00Z", console,
  };
  const start = runInNewContext(`${declarations.map(node => node.getText(ast)).join("\n")}\nwireIBEvents();\nstartDepthSubscription`, context);
  const client = {};
  function subscribe(isFutures = false, symbol = isFutures ? "ES" : "AAPL", owner: object = client) {
    const contract = { symbol, secType: isFutures ? "FUT" : "STK", exchange: isFutures ? "CME" : "SMART" };
    depthSubscribers.set(symbol, new Set([owner]));
    start(symbol, contract, { kind: isFutures ? "future" : "stock", isFutures, requestingClient: owner });
    return { symbol, contract };
  }
  function insert(id: number, position: number, isFutures: boolean, side = 1) {
    const event = isFutures ? "updateMktDepth" : "updateMktDepthL2";
    const args = isFutures
      ? [id, position, 0, side, 100 - position, position + 1]
      : [id, position, "ARCA", 0, side, 100 - position, position + 1, true];
    callbacks.get(event)!(...args);
  }
  return {
    subscribe, insert, callbacks, client, reqMktDepth, reqTickByTickData, bufferDepthForClient, symbolDepthStates, context,
    unsubscribe: (owner: object, symbol: string) => runInNewContext("unsubscribeClientFromDepth", context)(owner, symbol),
    restore: () => runInNewContext("restoreDepthSubscriptions", context)(),
  };
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

  it("opens no ticket and records nothing with depth disabled", () => {
    const relay = relayFixture({ enabled: false });
    relay.subscribe();
    expect(relay.reqMktDepth).not.toHaveBeenCalled();
    expect(relay.symbolDepthStates.size).toBe(0);
    expect(relay.callbacks.has("updateMktDepthL2")).toBe(false);
  });

  it("a subscribe made while IB is down gets its depth and tape on reconnect", () => {
    const relay = relayFixture({ connected: false });
    const { contract } = relay.subscribe();
    expect(relay.reqMktDepth).not.toHaveBeenCalled();
    relay.context.ibConnected = true;
    relay.restore();
    expect(relay.reqMktDepth).toHaveBeenCalledExactlyOnceWith(1, contract, 40, true);
    expect(relay.reqTickByTickData).toHaveBeenCalledExactlyOnceWith(2, contract, "AllLast", 0, false);
  });

  it("a subject refused for budget gets the ticket another session frees", () => {
    const relay = relayFixture();
    const others = [{}, {}, {}];
    others.forEach((owner, i) => relay.subscribe(false, `HELD${i}`, owner));
    expect(relay.reqMktDepth).toHaveBeenCalledTimes(3);
    const { contract } = relay.subscribe(false, "URTY");
    expect(relay.reqMktDepth).toHaveBeenCalledTimes(3);
    expect(relay.context.sendMessage).toHaveBeenCalledWith(relay.client, { type: "depth-unavailable", symbol: "URTY", reason: "depth-budget" });
    relay.unsubscribe(others[0], "HELD0");
    expect(relay.reqMktDepth).toHaveBeenCalledTimes(4);
    expect(relay.reqMktDepth.mock.calls.at(-1)!.slice(1)).toEqual([contract, 40, true]);
  });

  it("a freed ticket never retries a subject refused for entitlement", () => {
    const relay = relayFixture();
    const owner = {};
    relay.subscribe(false, "HELD", owner);
    relay.symbolDepthStates.set("NOENT", { depthTickerId: null, contract: {}, kind: "stock", isFutures: false, ladders: { bid: [], ask: [] }, focusedAt: 0 });
    relay.context.depthSubscribers.set("NOENT", new Set([relay.client]));
    relay.unsubscribe(owner, "HELD");
    expect(relay.reqMktDepth).toHaveBeenCalledTimes(1);
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
