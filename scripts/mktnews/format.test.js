import { describe, expect, it } from "vitest";

import { formatMessage, shouldEmit } from "./format.js";
import { parseFrame } from "./protocol.js";

const FLASH = parseFrame(
  JSON.stringify({
    type: "flash",
    data: {
      id: "flash-1",
      time: "2026-08-29T20:29:50.000Z",
      important: 1,
      data: { content: "Hormuz oil-route report denied." },
      impact: [{ impact: "bearish", symbol: "WTI" }],
    },
  }),
);

const TIME = parseFrame('{"type":"time","data":1788035440035}');

describe("shouldEmit", () => {
  it("drops time heartbeats unless --all", () => {
    expect(shouldEmit(TIME, { all: false })).toBe(false);
    expect(shouldEmit(TIME, { all: true })).toBe(true);
    expect(shouldEmit(FLASH, { all: false })).toBe(true);
  });
});

describe("formatMessage", () => {
  it("emits JSONL by default", () => {
    const line = formatMessage(FLASH, { pretty: false });
    const parsed = JSON.parse(line);
    expect(parsed.kind).toBe("flash");
    expect(parsed.data.data.content).toContain("Hormuz");
  });

  it("pretty-prints flash content on one line", () => {
    const line = formatMessage(FLASH, { pretty: true });
    expect(line).toContain("FLASH");
    expect(line).toContain("Hormuz oil-route report denied.");
    expect(line).toContain("WTI");
    expect(line.includes("\n")).toBe(false);
  });

  it("returns null when the filter drops the frame", () => {
    expect(formatMessage(TIME, { pretty: true, all: false })).toBeNull();
  });
});
