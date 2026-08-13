import { describe, expect, it } from "vitest";

import {
  COALESCABLE_KINDS,
  HARD_CAP_BYTES,
  SOFT_HIGH_WATER_BYTES,
  decideSend,
  isCoalescableKind,
  settleIncrementalBatch,
} from "./sendBackpressure.js";

const COALESCABLE = ["batch", "depth-batch", "tape-batch"];
const CRITICAL = [
  "ping",
  "pong",
  "status",
  "error",
  "price",
  "fundamentals",
  "snapshot",
  "subscribed",
  "unsubscribed",
  "searchResults",
  "depth-unavailable",
];

const RANK = { send: 0, drop: 1, close: 2 };

describe("decideSend — marks are ordered and non-empty", () => {
  it("puts the soft mark strictly below the hard cap", () => {
    expect(SOFT_HIGH_WATER_BYTES).toBeGreaterThan(0);
    expect(HARD_CAP_BYTES).toBeGreaterThan(SOFT_HIGH_WATER_BYTES);
  });
});

describe("decideSend — an idle socket always sends", () => {
  it.each([0, 1, 1024, SOFT_HIGH_WATER_BYTES - 1])("sends every kind at %i buffered bytes", (buffered) => {
    for (const kind of [...COALESCABLE, ...CRITICAL]) {
      expect(decideSend(buffered, kind)).toBe("send");
    }
  });

  it("treats an unusable bufferedAmount reading as empty rather than shedding", () => {
    for (const buffered of [undefined, null, NaN, -1, "not-a-number"]) {
      expect(decideSend(buffered, "batch")).toBe("send");
    }
  });

  it("reads a numeric string backlog (ws exposes a number, but never trust the shape)", () => {
    expect(decideSend(String(SOFT_HIGH_WATER_BYTES), "batch")).toBe("drop");
  });
});

describe("decideSend — soft high-water boundary", () => {
  it("drops a coalescable frame at EXACTLY the soft mark", () => {
    for (const kind of COALESCABLE) {
      expect(decideSend(SOFT_HIGH_WATER_BYTES, kind)).toBe("drop");
    }
  });

  it("still sends a coalescable frame one byte under the soft mark", () => {
    for (const kind of COALESCABLE) {
      expect(decideSend(SOFT_HIGH_WATER_BYTES - 1, kind)).toBe("send");
    }
  });

  it("keeps dropping coalescable frames all the way up to the hard cap", () => {
    expect(decideSend(SOFT_HIGH_WATER_BYTES + 1, "batch")).toBe("drop");
    expect(decideSend(HARD_CAP_BYTES - 1, "depth-batch")).toBe("drop");
  });
});

describe("decideSend — kind exemptions", () => {
  it("never drops a non-coalescable frame between the two marks", () => {
    for (const kind of CRITICAL) {
      expect(decideSend(SOFT_HIGH_WATER_BYTES, kind)).toBe("send");
      expect(decideSend(HARD_CAP_BYTES - 1, kind)).toBe("send");
    }
  });

  it("never drops an unknown/absent kind (fail safe: assume unrecoverable)", () => {
    expect(decideSend(SOFT_HIGH_WATER_BYTES, undefined)).toBe("send");
    expect(decideSend(SOFT_HIGH_WATER_BYTES, "some-future-message")).toBe("send");
  });

  it("classifies exactly the three snapshot batches as coalescable", () => {
    expect([...COALESCABLE_KINDS].sort()).toEqual([...COALESCABLE].sort());
    for (const kind of COALESCABLE) expect(isCoalescableKind(kind)).toBe(true);
    for (const kind of CRITICAL) expect(isCoalescableKind(kind)).toBe(false);
    expect(isCoalescableKind(undefined)).toBe(false);
  });
});

describe("decideSend — hard cap boundary", () => {
  it("closes at EXACTLY the hard cap regardless of kind", () => {
    for (const kind of [...COALESCABLE, ...CRITICAL, undefined]) {
      expect(decideSend(HARD_CAP_BYTES, kind)).toBe("close");
    }
  });

  it("closes above the hard cap regardless of kind", () => {
    for (const kind of [...COALESCABLE, ...CRITICAL]) {
      expect(decideSend(HARD_CAP_BYTES * 4, kind)).toBe("close");
    }
  });

  it("does not close one byte under the hard cap", () => {
    expect(decideSend(HARD_CAP_BYTES - 1, "ping")).toBe("send");
    expect(decideSend(HARD_CAP_BYTES - 1, "batch")).toBe("drop");
  });
});

describe("incremental batch settlement", () => {
  it("retains a dropped incremental batch for the next successful send", () => {
    const dirty = new Map([["SPY", { last: 600 }]]);
    const snapshot = Object.fromEntries(dirty);

    settleIncrementalBatch(dirty, snapshot, "drop");
    expect(Object.fromEntries(dirty)).toEqual(snapshot);

    settleIncrementalBatch(dirty, snapshot, "send");
    expect(dirty.size).toBe(0);
  });

  it("does not clear a newer update after an older snapshot is sent", () => {
    const oldQuote = { last: 600 };
    const dirty = new Map([["SPY", oldQuote]]);
    const snapshot = Object.fromEntries(dirty);
    dirty.set("SPY", { last: 601 });

    settleIncrementalBatch(dirty, snapshot, "send");

    expect(dirty.get("SPY")).toEqual({ last: 601 });
  });
});

describe("decideSend — monotonic in the backlog", () => {
  const probes = [
    0,
    1,
    SOFT_HIGH_WATER_BYTES - 2,
    SOFT_HIGH_WATER_BYTES - 1,
    SOFT_HIGH_WATER_BYTES,
    SOFT_HIGH_WATER_BYTES + 1,
    Math.floor((SOFT_HIGH_WATER_BYTES + HARD_CAP_BYTES) / 2),
    HARD_CAP_BYTES - 1,
    HARD_CAP_BYTES,
    HARD_CAP_BYTES + 1,
    HARD_CAP_BYTES * 10,
  ];

  it.each([...COALESCABLE, ...CRITICAL])("never relaxes as the backlog grows: %s", (kind) => {
    let previous = -1;
    for (const buffered of probes) {
      const rank = RANK[decideSend(buffered, kind)];
      expect(rank).toBeGreaterThanOrEqual(previous);
      previous = rank;
    }
  });

  it("is at least as strict on a coalescable frame as on a critical one at every level", () => {
    for (const buffered of probes) {
      expect(RANK[decideSend(buffered, "batch")]).toBeGreaterThanOrEqual(
        RANK[decideSend(buffered, "ping")],
      );
    }
  });
});
