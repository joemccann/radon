// R-312 / R-313 (REL-106): a long assistant turn is interruptible, and a
// proposal never carries a missing datum.
//
// (a) `streamMessage` walks the whole reply in 120-char chunks with an 8ms
//     sleep between each and no way to stop. A 400 KB reply is ~3,300 chunks
//     and ~27 seconds of forced typing that continues after the panel
//     unmounts, calling setMessages on every one of them.
// (b) `statementFor` guards `iv_rv_edge` with Number.isFinite and then
//     interpolates `range_score` raw one clause later, so a result missing it
//     renders the literal "range score undefined". `alternativesFrom` has the
//     same hole via `Math.round(row.score)` -> "SCORE NaN".

import { describe, expect, it, vi } from "vitest";

import { streamMessage, MAX_STREAM_CHUNKS } from "@/lib/chat";
import { buildScannerProposal } from "@/lib/agent/scannerProposal";

function result(over: Record<string, unknown> = {}) {
  return {
    ticker: "MU",
    verdict: "THETA_HARVEST",
    gates: { convexity: true, edge: true, risk: true },
    score: 82,
    setup: "",
    iv_rv_edge: 6.4,
    range_score: 71,
    structure: "PUT_CREDIT_SPREAD",
    ...over,
  } as never;
}

// T-272: the cap tests walk MAX_STREAM_CHUNKS chunks, each behind a real
// `await sleep(8)` in `streamMessage`. That is ~2.5s idle, and MEASURED on
// this runner at load average 112 it was 3935ms and 4894ms — the second
// within 106ms of vitest's 5000ms default, i.e. a load-shaped false red on
// the shared gate. Drive the same 240 sleeps on a fake clock instead: the
// assertions below are unchanged, only the wall clock is.
async function drainStream(text: string, setMessages: unknown) {
  vi.useFakeTimers();
  try {
    const done = streamMessage("m", text, setMessages as never, {
      signal: new AbortController().signal,
    });
    // One tick past the last chunk's sleep so the remainder write lands too.
    await vi.advanceTimersByTimeAsync(MAX_STREAM_CHUNKS * 8 + 8);
    await done;
  } finally {
    vi.useRealTimers();
  }
}

describe("(a) a stream can be stopped", () => {
  it("stops writing once the signal aborts", async () => {
    const controller = new AbortController();
    const writes: string[] = [];
    const setMessages = vi.fn((updater: never) => {
      if (typeof updater === "function") {
        (updater as (c: unknown[]) => unknown[])([{ id: "m", content: "" }]);
      }
      writes.push("w");
      if (writes.length === 3) controller.abort();
    });

    await streamMessage("m", "x".repeat(120 * 50), setMessages as never, {
      signal: controller.signal,
    });

    // A few writes may land in the same tick as the abort; the point is that
    // it stops far short of all 50 chunks.
    expect(writes.length).toBeLessThan(10);
  });

  it("caps the chunk count so a very large reply cannot type for a minute", async () => {
    expect(MAX_STREAM_CHUNKS).toBeGreaterThan(0);
    const writes: string[] = [];
    const setMessages = vi.fn(() => {
      writes.push("w");
    });

    // 400 KB — ~3,300 chunks unbounded.
    await drainStream("y".repeat(400_000), setMessages);
    // MAX_STREAM_CHUNKS animated writes plus ONE final write carrying the
    // remainder — that last write is what the next test's full-text guarantee
    // rests on. Unbounded this was ~3,300 writes.
    expect(writes.length).toBeLessThanOrEqual(MAX_STREAM_CHUNKS + 1);
    expect(writes.length).toBeGreaterThan(MAX_STREAM_CHUNKS);
  });

  it("still renders the FULL text despite the cap", async () => {
    let last = "";
    const setMessages = vi.fn((updater: never) => {
      const out = (updater as unknown as (c: unknown[]) => { content: string }[])([
        { id: "m", content: "" },
      ]);
      last = out[0].content;
    });
    const text = "z".repeat(400_000);
    await drainStream(text, setMessages);
    expect(last).toHaveLength(text.length);
  });
});

describe("(b) a proposal never states a datum it does not have", () => {
  it("does not render `range score undefined`", () => {
    const proposal = buildScannerProposal([result({ range_score: undefined })]);
    expect(JSON.stringify(proposal ?? {})).not.toContain("undefined");
  });

  it("does not render `range score NaN`", () => {
    const proposal = buildScannerProposal([result({ range_score: Number.NaN })]);
    expect(JSON.stringify(proposal ?? {})).not.toContain("NaN");
  });

  it("does not render `SCORE NaN` in the alternatives", () => {
    const proposal = buildScannerProposal([
      result(),
      result({ ticker: "AMD", score: undefined }),
    ]);
    const metas = (proposal?.alternatives ?? []).map((a) => a.meta);
    expect(metas.join("|")).not.toContain("NaN");
  });

  it("leaves a complete result exactly as it was", () => {
    const proposal = buildScannerProposal([result(), result({ ticker: "AMD", score: 77 })]);
    expect(proposal?.statement).toContain("71");
    expect((proposal?.alternatives ?? [])[0]?.meta).toBe("SCORE 77");
  });
});
