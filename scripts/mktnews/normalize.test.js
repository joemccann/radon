import { describe, expect, it } from "vitest";

import { parseFrame } from "./protocol.js";
import { MAX_CONTENT_CHARS, containsUpstreamHost, toHeadline } from "./normalize.js";

const FLASH = parseFrame(
  JSON.stringify({
    type: "flash",
    data: {
      id: "01a04f36-dd6b-7118-83fb-bf77195e2f55",
      type: 0,
      time: "2026-08-29T20:35:56.000Z",
      important: 1,
      data: { title: null, content: "Explosions heard in Kyiv." },
      impact: [{ impact: "bearish", symbol: "WTI" }],
    },
  }),
);

describe("toHeadline", () => {
  it("drops time heartbeats", () => {
    expect(toHeadline(parseFrame('{"type":"time","data":1}'))).toBeNull();
  });

  it("projects a client-safe headline", () => {
    expect(toHeadline(FLASH)).toEqual({
      kind: "headline",
      id: "01a04f36-dd6b-7118-83fb-bf77195e2f55",
      time: "2026-08-29T20:35:56.000Z",
      important: true,
      content: "Explosions heard in Kyiv.",
      impact: [{ symbol: "WTI", impact: "bearish" }],
    });
  });

  it("omits missing id or content", () => {
    expect(toHeadline(parseFrame(JSON.stringify({ type: "flash", data: { id: "x", data: {} } })))).toBeNull();
  });

  it("clips oversized content", () => {
    const msg = parseFrame(
      JSON.stringify({
        type: "flash",
        data: { id: "big", data: { content: "K".repeat(MAX_CONTENT_CHARS + 40) } },
      }),
    );
    expect(toHeadline(msg).content.length).toBe(MAX_CONTENT_CHARS);
  });

  it("does not copy raw upstream envelopes", () => {
    const item = toHeadline(FLASH);
    expect(item).not.toHaveProperty("raw");
    expect(item).not.toHaveProperty("data");
    expect(containsUpstreamHost(item)).toBe(false);
  });
});
