import { describe, expect, it } from "vitest";

import { createForwardSubRegistry } from "./forwardSubRegistry.js";

const FWD = "@fwd:VIX:20260916";
const FWD_OCT = "@fwd:VIX:20261021";

describe("createForwardSubRegistry — start/stop edges", () => {
  it("starts on the first holder and stops on the last", () => {
    const registry = createForwardSubRegistry();

    expect(registry.acquire(FWD, "c1|VIX_20260916_20_C")).toEqual({ shouldStart: true });
    expect(registry.release(FWD, "c1|VIX_20260916_20_C")).toEqual({ shouldStop: true });
    expect(registry.isEmpty()).toBe(true);
  });

  it("does not start again for a second holder", () => {
    const registry = createForwardSubRegistry();

    expect(registry.acquire(FWD, "c1|VIX_20260916_20_C").shouldStart).toBe(true);
    expect(registry.acquire(FWD, "c2|VIX_20260916_20_C").shouldStart).toBe(false);
    expect(registry.acquire(FWD, "c1|VIX_20260916_25_C").shouldStart).toBe(false);
    expect(registry.holderCount(FWD)).toBe(3);
  });

  it("keeps the line while any holder remains, and stops only on the last release", () => {
    const registry = createForwardSubRegistry();
    registry.acquire(FWD, "c1|VIX_20260916_20_C");
    registry.acquire(FWD, "c2|VIX_20260916_20_C");
    registry.acquire(FWD, "c1|VIX_20260916_25_C");

    expect(registry.release(FWD, "c1|VIX_20260916_20_C").shouldStop).toBe(false);
    expect(registry.release(FWD, "c2|VIX_20260916_20_C").shouldStop).toBe(false);
    expect(registry.holderCount(FWD)).toBe(1);
    expect(registry.release(FWD, "c1|VIX_20260916_25_C").shouldStop).toBe(true);
    expect(registry.holderCount(FWD)).toBe(0);
    expect(registry.isEmpty()).toBe(true);
  });

  it("is idempotent per owner: a re-subscribe cannot inflate the refcount", () => {
    const registry = createForwardSubRegistry();
    registry.acquire(FWD, "c1|VIX_20260916_20_C");

    expect(registry.acquire(FWD, "c1|VIX_20260916_20_C").shouldStart).toBe(false);
    expect(registry.holderCount(FWD)).toBe(1);
    // One release still tears it down — no phantom second hold.
    expect(registry.release(FWD, "c1|VIX_20260916_20_C").shouldStop).toBe(true);
    expect(registry.isEmpty()).toBe(true);
  });

  it("re-acquiring after a full release starts the line again", () => {
    const registry = createForwardSubRegistry();
    registry.acquire(FWD, "c1|VIX");
    registry.release(FWD, "c1|VIX");

    expect(registry.acquire(FWD, "c1|VIX").shouldStart).toBe(true);
  });

  it("tracks keys independently", () => {
    const registry = createForwardSubRegistry();
    registry.acquire(FWD, "c1|VIX_20260916_20_C");

    expect(registry.acquire(FWD_OCT, "c1|VIX_20261021_20_C").shouldStart).toBe(true);
    expect(registry.release(FWD, "c1|VIX_20260916_20_C").shouldStop).toBe(true);
    expect(registry.holderCount(FWD_OCT)).toBe(1);
    expect(registry.keys()).toEqual([FWD_OCT]);
    expect(registry.isEmpty()).toBe(false);
  });
});

describe("createForwardSubRegistry — releases that must not corrupt state", () => {
  it("never reports a stop for a key it does not track", () => {
    const registry = createForwardSubRegistry();

    expect(registry.release(FWD, "ghost")).toEqual({ shouldStop: false });
    expect(registry.isEmpty()).toBe(true);
    expect(registry.keys()).toEqual([]);
  });

  it("never reports a stop for an owner that does not hold the key", () => {
    const registry = createForwardSubRegistry();
    registry.acquire(FWD, "c1|VIX");

    expect(registry.release(FWD, "c2|VIX")).toEqual({ shouldStop: false });
    expect(registry.holderCount(FWD)).toBe(1);
  });

  it("double release stops exactly once", () => {
    const registry = createForwardSubRegistry();
    registry.acquire(FWD, "c1|VIX");

    expect(registry.release(FWD, "c1|VIX").shouldStop).toBe(true);
    expect(registry.release(FWD, "c1|VIX").shouldStop).toBe(false);
    expect(registry.isEmpty()).toBe(true);
  });

  it("holderCount is 0 for an unknown key", () => {
    expect(createForwardSubRegistry().holderCount("nope")).toBe(0);
  });
});

describe("createForwardSubRegistry — releaseAllForOwner", () => {
  it("releases every key one owner holds and reports the ones that stopped", () => {
    const registry = createForwardSubRegistry();
    const owner = "c1|VIX_20260916_20_C";
    registry.acquire(FWD, owner);
    registry.acquire(`@curve:${FWD}|20260916`, owner);
    registry.acquire(FWD_OCT, "c2|VIX_20261021_20_C");

    expect(registry.releaseAllForOwner(owner).sort()).toEqual([`@curve:${FWD}|20260916`, FWD].sort());
    expect(registry.holderCount(FWD)).toBe(0);
    expect(registry.holderCount(FWD_OCT)).toBe(1);
  });

  it("reports only the keys whose LAST holder went away", () => {
    const registry = createForwardSubRegistry();
    registry.acquire(FWD, "c1|VIX_20260916_20_C");
    registry.acquire(FWD, "c2|VIX_20260916_20_C");
    registry.acquire(FWD_OCT, "c1|VIX_20261021_20_C");

    expect(registry.releaseAllForOwner("c1|VIX_20260916_20_C")).toEqual([]);
    expect(registry.releaseAllForOwner("c1|VIX_20261021_20_C")).toEqual([FWD_OCT]);
    expect(registry.holderCount(FWD)).toBe(1);
  });

  it("returns [] for an owner with no holds and leaves no residue", () => {
    const registry = createForwardSubRegistry();

    expect(registry.releaseAllForOwner("ghost")).toEqual([]);
    expect(registry.isEmpty()).toBe(true);
  });

  it("is idempotent", () => {
    const registry = createForwardSubRegistry();
    registry.acquire(FWD, "c1|VIX");

    expect(registry.releaseAllForOwner("c1|VIX")).toEqual([FWD]);
    expect(registry.releaseAllForOwner("c1|VIX")).toEqual([]);
    expect(registry.isEmpty()).toBe(true);
  });

  it("drops one client's holds without touching another client's", () => {
    const registry = createForwardSubRegistry();
    registry.acquire(FWD, "c1|VIX_20260916_20_C");
    registry.acquire(FWD, "c2|VIX_20260916_20_C");
    registry.acquire(`@curve:${FWD}|20260916`, "c1|VIX_20260916_20_C");
    registry.acquire(`@curve:${FWD}|20260916`, "c2|VIX_20260916_20_C");

    registry.releaseAllForOwner("c1|VIX_20260916_20_C");

    expect(registry.owners()).toEqual(["c2|VIX_20260916_20_C"]);
    expect(registry.holderCount(FWD)).toBe(1);
    expect(registry.holderCount(`@curve:${FWD}|20260916`)).toBe(1);
  });
});

describe("createForwardSubRegistry — churn leaves nothing behind", () => {
  it("survives 200 subscribe/unsubscribe cycles with both maps empty and every line stopped once", () => {
    const registry = createForwardSubRegistry();
    // Mirrors the relay's shape: two clients, three option subjects per client
    // spread over two future expiries, plus the front-month key and one curve
    // hold per subject.
    const started = new Map();
    const stopped = new Map();
    const bump = (map, key) => map.set(key, (map.get(key) ?? 0) + 1);

    for (let cycle = 0; cycle < 200; cycle += 1) {
      const owners = [];
      for (const client of ["c1", "c2"]) {
        for (const [optExp, futExp] of [
          ["20260910", "20260916"],
          ["20260916", "20260916"],
          ["20261015", "20261021"],
        ]) {
          const subject = `VIX_${optExp}_20_C`;
          const owner = `${client}|${subject}`;
          const fwdKey = `@fwd:VIX:${futExp}`;
          if (registry.acquire(fwdKey, owner).shouldStart) bump(started, fwdKey);
          const curveKey = `@curve:${fwdKey}|${optExp}`;
          if (registry.acquire(curveKey, owner).shouldStart) bump(started, curveKey);
          owners.push(owner);
        }
        const indexOwner = `${client}|VIX`;
        if (registry.acquire("@fwd:VIX", indexOwner).shouldStart) bump(started, "@fwd:VIX");
        owners.push(indexOwner);
      }

      expect(registry.holderCount("@fwd:VIX:20260916")).toBe(4);
      expect(registry.holderCount("@fwd:VIX:20261021")).toBe(2);
      expect(registry.holderCount("@fwd:VIX")).toBe(2);

      for (const owner of owners) {
        for (const key of registry.releaseAllForOwner(owner)) bump(stopped, key);
      }

      expect(registry.isEmpty()).toBe(true);
    }

    // Every key opened exactly once per cycle and closed exactly once per cycle:
    // no leaked line, no double cancel.
    expect([...started.keys()].sort()).toEqual([...stopped.keys()].sort());
    for (const [key, count] of started) {
      expect(count).toBe(200);
      expect(stopped.get(key)).toBe(200);
    }
    expect(registry.keys()).toEqual([]);
    expect(registry.owners()).toEqual([]);
    expect(registry.isEmpty()).toBe(true);
  });

  it("survives interleaved partial churn (one client cycling while another holds)", () => {
    const registry = createForwardSubRegistry();
    registry.acquire(FWD, "c1|VIX_20260916_20_C");

    for (let cycle = 0; cycle < 200; cycle += 1) {
      expect(registry.acquire(FWD, "c2|VIX_20260916_25_C").shouldStart).toBe(false);
      expect(registry.releaseAllForOwner("c2|VIX_20260916_25_C")).toEqual([]);
      expect(registry.holderCount(FWD)).toBe(1);
    }

    expect(registry.releaseAllForOwner("c1|VIX_20260916_20_C")).toEqual([FWD]);
    expect(registry.isEmpty()).toBe(true);
  });
});
