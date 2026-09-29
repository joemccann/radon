/**
 * Relay wiring for option session volume.
 *
 * Generic tick 233 (RT Volume) is requested on every live reqMktData, but IB
 * delivers it as tickString type 48 — not tickPrice / tickSize. Volume also
 * arrives as tickSize type 8. Both paths must land in PriceData and be
 * broadcast; otherwise a thin option whose book is already quoted (bid/ask
 * tickPrice) keeps volume=null forever and the order sheet renders VOLUME ---.
 *
 * HIGH/LOW stay tickPrice 6/7 (delayed 72/73). This file does not invent them.
 */
import { describe, expect, it, vi } from "vitest";
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
import ts from "typescript";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = resolve(fileURLToPath(import.meta.url), "..");
const projectRoot = resolve(__dirname, "..", "..");
const source = readFileSync(resolve(projectRoot, "scripts", "ib_realtime_server.js"), "utf8");
const handlerPath = new URL("../../scripts/ib_tick_handler.js", import.meta.url).pathname;
const {
  createPriceData,
  parseRtVolume,
  updatePriceFromTickString,
  updatePriceFromTickSize,
} = await import(handlerPath);

const RT_VOLUME = 48;

function relayFixture() {
  // Execute actual declarations without importing the socket-starting entry point.
  const ast = ts.createSourceFile("relay.js", source, ts.ScriptTarget.Latest, true, ts.ScriptKind.JS);
  const names = new Set(["ensureSymbolState", "startLiveSubscription", "wireIBEvents", "onTickSize", "onTickString", "hydrateAndBroadcast"]);
  const declarations = ast.statements.filter((node) => ts.isFunctionDeclaration(node) && node.name && names.has(node.name.text));
  expect(declarations).toHaveLength(names.size);
  const callbacks = new Map<string, (...args: unknown[]) => void>();
  const client = {};
  const symbol = "SNDK_20261218_175_C";
  const symbolStates = new Map();
  const snapshotRequests = new Map();
  const bufferPriceForClient = vi.fn();
  const reqMktData = vi.fn();
  const context = {
    ib: { on: (name: string, callback: (...args: unknown[]) => void) => callbacks.set(name, callback), reqMktData },
    EventName: new Proxy({}, { get: (_target, key) => key }),
    ibClientGeneration: 1, ibConnected: true, nextRequestId: 0, DEPTH_ENABLED: false,
    symbolStates, snapshotRequests, requestIdToSymbol: new Map(),
    symbolSubscribers: new Map([[symbol, new Set([client])]]),
    optionSessionCache: new Map(), seedOptionSessionMark: vi.fn(), requestFundamentals: vi.fn(),
    createPriceData, updatePriceFromTickSize, updatePriceFromTickString,
    markTick: vi.fn(), verbose: vi.fn(), applyCachedClose: vi.fn(), bufferPriceForClient,
    nowIso: () => "2026-09-29T15:00:00Z", console,
  };
  const subscribe = runInNewContext(`${declarations.map((node) => node.getText(ast)).join("\n")}
wireIBEvents();
startLiveSubscription`, context);
  const contract = { symbol: "SNDK", secType: "OPT", strike: 175, right: "C", expiry: "20261218" };
  subscribe(symbol, contract);
  return { callbacks, client, symbol, contract, symbolStates, snapshotRequests, bufferPriceForClient, reqMktData };
}

describe("ib_realtime_server.js — RT_VOLUME + tickSize broadcast", () => {
  it("requests generic ticks 233 and 165 on live subscriptions", () => {
    const fixture = relayFixture();
    expect(fixture.reqMktData).toHaveBeenCalledExactlyOnceWith(1, fixture.contract, "233,165", false, false);
  });

  it.each([
    ["tickSize", 8, 17],
    ["tickString", RT_VOLUME, "4.20;1;1694438400;17;4.18;true"],
  ])("registered %s updates and broadcasts option volume without a price tick", (event, tickType, value) => {
    const fixture = relayFixture();
    const callback = fixture.callbacks.get(event as string);
    expect(callback).toBeTypeOf("function");
    callback!(1, tickType, value);
    expect(fixture.symbolStates.get(fixture.symbol).data.volume).toBe(17);
    expect(fixture.bufferPriceForClient).toHaveBeenCalledExactlyOnceWith(
      fixture.client, fixture.symbol, expect.objectContaining({ volume: 17 }),
    );
    // Delivery takes a snapshot, not a mutable reference to later ticks.
    fixture.symbolStates.get(fixture.symbol).data.volume = 23;
    expect(fixture.bufferPriceForClient.mock.calls[0][2].volume).toBe(17);
  });

  it("ignores unrelated string ticks and unknown request IDs", () => {
    const fixture = relayFixture();
    fixture.callbacks.get("tickString")!(1, 99, "17");
    fixture.callbacks.get("tickString")!(999, RT_VOLUME, "4.20;1;1694438400;17;4.18;true");
    fixture.callbacks.get("tickSize")!(999, 8, 17);
    expect(fixture.symbolStates.get(fixture.symbol).data.volume).toBeNull();
    expect(fixture.bufferPriceForClient).not.toHaveBeenCalled();
  });
});

describe("parseRtVolume — SNDK-shaped option payload", () => {
  it("reads contract volume from the 4th semicolon field", () => {
    expect(parseRtVolume("4.20;1;1694438400;17;4.18;true")).toBe(17);
    expect(updatePriceFromTickString(
      createPriceData("SNDK_20260925_1750_C"),
      RT_VOLUME,
      "4.20;1;1694438400;17;4.18;true",
    )).toBe(true);
  });
});
