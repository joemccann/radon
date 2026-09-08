import { describe, expect, it } from "vitest";
import { NEWSFEED_VOICE_SYSTEM, parseVoiceCopy } from "../lib/newsfeedVoice";

const source = { title: "Seasonality", content: "Median rises from 104 to 130 in Year 3, around month +9." };
describe("newsfeed voice", () => {
  it("uses a provisional evidenced voice with factual and instruction boundaries", () => {
    expect(NEWSFEED_VOICE_SYSTEM).toMatch(/provisional/i);
    expect(NEWSFEED_VOICE_SYSTEM).toMatch(/untrusted/i);
    expect(NEWSFEED_VOICE_SYSTEM).toMatch(/holdings/i);
    expect(NEWSFEED_VOICE_SYSTEM).toMatch(/units/i);
  });
  it("sanitizes publisher text and computes a caption from validated fields", () => {
    expect(parseVoiceCopy(JSON.stringify({ title: "Seasonality", content: "104 to 130. Year 3, month +9.\nSource: ZeroHedge" }), source))
      .toEqual({ title: "Seasonality", content: "104 to 130. Year 3, month +9.", caption: "Seasonality\n\n104 to 130. Year 3, month +9." });
  });
  it.each(["oops", "null", '[]', '{"title":"","content":"ok"}', '{"title":"ok","content":1}', '{"title":"ok","content":"The Market Ear"}'])
    ("rejects malformed or empty output %s", raw => expect(() => parseVoiceCopy(raw, source)).toThrow());
  it("rejects numerical claims absent from the input", () => {
    expect(() => parseVoiceCopy('{"title":"Up 25%","content":"Seasonality."}', source)).toThrow();
  });
  it("rejects changed numeric units", () => {
    expect(() => parseVoiceCopy('{"title":"Seasonality","content":"130% in Year 3."}', source)).toThrow();
  });
});
