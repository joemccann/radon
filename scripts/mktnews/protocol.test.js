import { describe, expect, it } from "vitest";

import {
  DEFAULT_FLASH_URL,
  DEFAULT_URL,
  classifyMessage,
  parseFrame,
} from "./protocol.js";

describe("mktnews protocol", () => {
  it("pins the public English websocket URL", () => {
    expect(DEFAULT_URL).toBe("wss://api.mktnews.net/?lang=en");
  });

  it("pins the public English flash history URL", () => {
    expect(DEFAULT_FLASH_URL).toBe("https://api.mktnews.net/api/flash?lang=en");
  });

  it("classifies server time heartbeats", () => {
    const msg = parseFrame('{"type":"time","data":1788035440035}');
    expect(msg.kind).toBe("time");
    expect(msg.type).toBe("time");
    expect(msg.data).toBe(1788035440035);
    expect(classifyMessage(msg)).toBe("time");
  });

  it("classifies flash envelopes", () => {
    const raw = JSON.stringify({
      type: "flash",
      data: {
        id: "01a04f36-dd6b-7118-83fb-bf77195e2f55",
        type: 0,
        time: "2026-08-29T20:29:50.000Z",
        important: 1,
        data: { title: null, content: "Hormuz oil-route report denied." },
        impact: [{ impact: "bearish", symbol: "WTI" }],
      },
    });
    const msg = parseFrame(raw);
    expect(msg.kind).toBe("flash");
    expect(msg.data.id).toBe("01a04f36-dd6b-7118-83fb-bf77195e2f55");
    expect(msg.data.data.content).toContain("Hormuz");
  });

  it("classifies a bare flash item (no envelope type string)", () => {
    const msg = parseFrame(
      JSON.stringify({
        id: "abc",
        type: 0,
        time: "2026-08-29T20:29:50.000Z",
        important: 0,
        data: { title: "", content: "UAE inspects Banque Misr branch." },
      }),
    );
    expect(msg.kind).toBe("flash");
    expect(msg.data.data.content).toContain("Banque Misr");
  });

  it("classifies news envelopes", () => {
    const msg = parseFrame(
      JSON.stringify({ type: "news", data: { id: "n1", title: "Summit" } }),
    );
    expect(msg.kind).toBe("news");
    expect(msg.data.title).toBe("Summit");
  });

  it("returns raw for invalid JSON", () => {
    const msg = parseFrame("not-json");
    expect(msg.kind).toBe("raw");
    expect(msg.raw).toBe("not-json");
  });

  it("decodes Buffer frames as utf8", () => {
    const msg = parseFrame(Buffer.from('{"type":"time","data":1}'));
    expect(msg.kind).toBe("time");
    expect(msg.data).toBe(1);
  });
});
