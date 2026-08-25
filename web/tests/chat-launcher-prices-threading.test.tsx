/**
 * @vitest-environment jsdom
 *
 * Assistant order surface — the quote map actually reaches the gate.
 *
 * chat-approval-gate-quote-telemetry.test.tsx proves ChatPanel renders the
 * shared nine-field block once it HAS a `prices` map. That is only half the
 * story: ChatPanel does not own quotes and never opens its own relay socket,
 * so if nothing hands it the map the gate silently renders the empty
 * "No real-time data" panel in the running app and the operator confirms a
 * live order with no bid/ask in front of them.
 *
 * The map already exists exactly once, in WorkspaceShell (`usePrices` plus the
 * previous-close backfill). Pinned here is the path from that single owner to
 * the gate: WorkspaceShell -> ChatLauncher -> ChatPanel.
 */
import React from "react";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render } from "@testing-library/react";

import type { PriceData } from "@/lib/pricesProtocol";

const chatPanelProps = vi.hoisted(() => ({ current: null as Record<string, unknown> | null }));

vi.mock("@/components/ChatPanel", () => ({
  default: (props: Record<string, unknown>) => {
    chatPanelProps.current = props;
    return <div data-testid="chat-panel" />;
  },
}));

import ChatLauncher from "@/components/ChatLauncher";

afterEach(() => {
  cleanup();
  chatPanelProps.current = null;
});

const projectRoot = resolve(__dirname, "..");

const MU: PriceData = {
  symbol: "MU",
  last: 121.5,
  lastIsCalculated: false,
  bid: 121.4,
  ask: 121.6,
  bidSize: 4,
  askSize: 7,
  volume: 8_100_000,
  high: 123.1,
  low: 119.8,
  open: 120.2,
  close: 120.0,
  week52High: null,
  week52Low: null,
  avgVolume: null,
  delta: null,
  gamma: null,
  theta: null,
  vega: null,
  impliedVol: null,
  undPrice: null,
  timestamp: new Date().toISOString(),
} as PriceData;

describe("assistant gate quote plumbing", () => {
  it("ChatLauncher forwards the live quote map to ChatPanel", () => {
    render(
      <ChatLauncher
        activeSection="dashboard"
        portfolio={{ positions: [] } as never}
        prices={{ MU }}
      />,
    );

    fireEvent.keyDown(document, { key: "j", ctrlKey: true });

    expect(chatPanelProps.current).not.toBeNull();
    expect(chatPanelProps.current?.prices).toEqual({ MU });
  });

  it("WorkspaceShell hands ChatLauncher the one price map it already owns", () => {
    const source = readFileSync(resolve(projectRoot, "components", "WorkspaceShell.tsx"), "utf8");

    const launcher = source.match(/<ChatLauncher[\s\S]*?\/>/);
    expect(launcher).not.toBeNull();
    // The backfilled map (`const prices = usePreviousClose(...)`), not the raw
    // socket map — the gate's DAY row needs the previous close.
    expect(launcher?.[0]).toContain("prices={prices}");
    expect(source).toContain("const prices = usePreviousClose(");
  });
});
