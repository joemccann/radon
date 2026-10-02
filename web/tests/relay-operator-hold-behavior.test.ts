import { readFileSync } from "node:fs";
import { createContext, runInContext } from "node:vm";
import { describe, expect, it, vi } from "vitest";
import ts from "typescript";
import * as machine from "../../scripts/lib/staleDataMachine.js";

const source = readFileSync(new URL("../../scripts/ib_realtime_server.js", import.meta.url), "utf8");
const NOW = Date.parse("2026-10-01T15:00:00Z");

function relay() {
  // Run the actual poller and actual timer body, without the socket entry point.
  const ast = ts.createSourceFile("relay.js", source, ts.ScriptTarget.Latest, true, ts.ScriptKind.JS);
  const functions = new Set(["refreshOperatorHold", "requestGatewayRestart"]);
  const constants = new Set([
    "IB_HEALTH_LITE_URL", "IB_RESTART_URL", "IB_RESTART_TIMEOUT_MS", "operatorHoldActive",
  ]);
  const declarations = ast.statements.filter(node =>
    (ts.isFunctionDeclaration(node) && node.name && functions.has(node.name.text))
    || (ts.isVariableStatement(node) && node.declarationList.declarations.some(declaration =>
      ts.isIdentifier(declaration.name) && constants.has(declaration.name.text))),
  );
  const timers = ast.statements.filter(node =>
    ts.isExpressionStatement(node) && ts.isBinaryExpression(node.expression)
    && ts.isIdentifier(node.expression.left) && node.expression.left.text === "staleCheckTimer",
  );
  expect(declarations).toHaveLength(functions.size + constants.size);
  expect(timers).toHaveLength(1);
  const fetch = vi.fn();
  const writeRelayHealth = vi.fn();
  const reconnectIBSocket = vi.fn();
  const resubscribeAll = vi.fn();
  const escalateStaleData = vi.fn();
  let tick: () => void = () => { throw new Error("stale timer was not registered"); };
  const context = createContext({
    ...machine,
    process: { env: {} },
    Date: { now: () => NOW }, AbortSignal,
    fetch, writeRelayHealth, reconnectIBSocket, resubscribeAll, escalateStaleData,
    resubscribeStaleSubjects: vi.fn(),
    setInterval: (callback: () => void) => { tick = callback; return 1; },
    console: { log: vi.fn(), warn: vi.fn() },
    shuttingDown: false, ibGatewayRestarting: false, ibConnected: false,
    isUSMarketHours: () => true,
    symbolSubscribers: new Map([["SPY", new Set([{}])]]),
    symbolStates: new Map([["SPY", { tickerId: 1, lastTickAt: NOW - 120_000 }]]),
    staleReconnectCycles: machine.MAX_RECONNECT_CYCLES, lastFarmStateCode: null, lastEscalationAt: 0,
    relayHealthInError: true, lastTickHeartbeatAt: 0,
    lastTickTimestamp: NOW - 120_000, ibDisconnectedSinceAt: NOW - 120_000,
  });
  runInContext([...declarations, ...timers].map(node => node.getText(ast)).join("\n"), context);
  return {
    fetch, writeRelayHealth, reconnectIBSocket, resubscribeAll, escalateStaleData, context,
    tick: () => tick(),
    refresh: () => runInContext("refreshOperatorHold()", context) as Promise<void>,
    restart: () => runInContext("requestGatewayRestart()", context) as Promise<void>,
    held: () => runInContext("operatorHoldActive", context) as boolean,
  };
}

function response(body: unknown, status = 200) {
  return { ok: status < 400, status, json: async () => body };
}

function expectHeld(r: ReturnType<typeof relay>) {
  r.writeRelayHealth.mockClear();
  r.tick();
  expect(r.held()).toBe(true);
  expect(r.writeRelayHealth).toHaveBeenCalledExactlyOnceWith("ok", expect.objectContaining({ reason: "operator_hold" }));
  expect(r.reconnectIBSocket).not.toHaveBeenCalled();
  expect(r.resubscribeAll).not.toHaveBeenCalled();
  expect(r.escalateStaleData).not.toHaveBeenCalled();
  expect(r.context.staleReconnectCycles).toBe(0);
}

describe("T-527: relay operator hold from transport to recovery timer", () => {
  it.each([false, true])("stands down on a held health response with connected=%s", async connected => {
    const r = relay();
    r.context.ibConnected = connected;
    r.fetch.mockResolvedValue(response({ operator_hold: true }));
    await r.refresh();
    expect(r.fetch).toHaveBeenCalledExactlyOnceWith("http://127.0.0.1:8321/health/lite", { signal: expect.any(AbortSignal) });
    expectHeld(r);
  });

  it("only a confirmed clear restores ordinary disconnected health", async () => {
    const r = relay();
    r.fetch.mockResolvedValueOnce(response({ operator_hold: true }));
    await r.refresh();
    expectHeld(r);
    r.fetch.mockResolvedValueOnce(response({ operator_hold: false }));
    await r.refresh();
    r.writeRelayHealth.mockClear();
    r.tick();
    expect(r.held()).toBe(false);
    expect(r.writeRelayHealth).toHaveBeenCalledExactlyOnceWith("error", expect.objectContaining({ reason: "ib_disconnected" }));
  });

  it.each([{}, null, { operator_hold: null }, { operator_hold: "false" }])(
    "retains the hold when a successful health response has unknown state: %j", async body => {
      const r = relay();
      r.fetch.mockResolvedValueOnce(response({ operator_hold: true }));
      await r.refresh();
      r.fetch.mockResolvedValueOnce(response(body));
      await r.refresh();
      expectHeld(r);
    },
  );

  it.each(["http", "network", "json"])("retains the hold after a %s failure", async failure => {
    const r = relay();
    r.fetch.mockResolvedValueOnce(response({ operator_hold: true }));
    await r.refresh();
    if (failure === "network") r.fetch.mockRejectedValueOnce(new Error("offline"));
    else if (failure === "json") r.fetch.mockResolvedValueOnce({ ok: true, json: async () => { throw new Error("bad json"); } });
    else r.fetch.mockResolvedValueOnce(response({ operator_hold: false }, 503));
    await r.refresh();
    expectHeld(r);
  });

  it.each([[423, {}], [409, { detail: { code: "OPERATOR_HOLD" } }]] as const)(
    "a restart refusal %s latches the hold for the next timer cycle", async (status, body) => {
      const r = relay();
      r.fetch.mockResolvedValueOnce(response(body, status));
      await r.restart();
      expect(r.fetch).toHaveBeenCalledExactlyOnceWith("http://127.0.0.1:8321/ib/restart", {
        method: "POST", signal: expect.any(AbortSignal),
      });
      expectHeld(r);
    },
  );
});
