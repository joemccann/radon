import { describe, expect, it } from "vitest";
import {
  encodeVoiceEvent,
  readVoiceRewrite,
  voiceProgress,
  type VoiceStage,
} from "../lib/newsfeedVoiceProgress";

function sseResponse(chunks: string[]): Response {
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
  return new Response(body, { headers: { "content-type": "text/event-stream; charset=utf-8" } });
}

describe("voiceProgress", () => {
  it("rises monotonically while drafting and never reaches done", () => {
    const samples = [0, 1_000, 5_000, 15_000, 30_000, 90_000].map(ms => voiceProgress("drafting", ms));
    samples.slice(1).forEach((value, index) => expect(value).toBeGreaterThan(samples[index]));
    expect(samples.at(-1)).toBeLessThan(voiceProgress("checking", 0));
    expect(voiceProgress("checking", 0)).toBeLessThan(1);
  });

  it("keeps hedging on the same curve as drafting", () => {
    expect(voiceProgress("hedging", 20_000)).toBe(voiceProgress("drafting", 20_000));
  });

  it("starts above zero so the bar is visible immediately", () => {
    expect(voiceProgress("queued", 0)).toBeGreaterThan(0);
  });
});

describe("readVoiceRewrite", () => {
  it("reports each stage and returns the result, even when frames split across chunks", async () => {
    const wire = encodeVoiceEvent("stage", { stage: "queued" })
      + encodeVoiceEvent("stage", { stage: "drafting" })
      + encodeVoiceEvent("result", { title: "T", content: "C" });
    const stages: VoiceStage[] = [];
    const result = await readVoiceRewrite(sseResponse([wire.slice(0, 7), wire.slice(7, 40), wire.slice(40)]), stage => stages.push(stage));
    expect(stages).toEqual(["queued", "drafting"]);
    expect(result).toEqual({ title: "T", content: "C" });
  });

  it("throws the server's error event", async () => {
    const wire = encodeVoiceEvent("error", { error: "Rewrite timed out. Try again.", status: 504 });
    await expect(readVoiceRewrite(sseResponse([wire]), () => {})).rejects.toThrow("Rewrite timed out. Try again.");
  });

  it("throws when the stream ends without a result", async () => {
    await expect(readVoiceRewrite(sseResponse([encodeVoiceEvent("stage", { stage: "drafting" })]), () => {})).rejects.toThrow();
  });

  it("reads a plain JSON body", async () => {
    const response = new Response(JSON.stringify({ title: "T", content: "C" }), { headers: { "content-type": "application/json" } });
    await expect(readVoiceRewrite(response, () => {})).resolves.toEqual({ title: "T", content: "C" });
  });
});
