// @vitest-environment jsdom
//
// The armed-for-payload machine shared by every order ticket's confirm step.
// A boolean `confirmStep` stays true while the order under it changes, so the
// operator reviews one order and transmits another. Arming captures a key of
// the reviewed payload; any change to the payload disarms, and the unbounded
// acknowledgement belongs to the armed payload alone.

import { afterEach, describe, expect, it } from "vitest";
import { act, cleanup, renderHook } from "@testing-library/react";

import {
  COMPOSE,
  armedConfirmTransition,
  canonicalPayloadKey,
  useArmedConfirm,
  type ArmedConfirmState,
} from "../lib/orders/useArmedConfirm";

afterEach(() => cleanup());

describe("armedConfirmTransition", () => {
  const armedA: ArmedConfirmState = { phase: "armed", key: "A", acknowledged: false };

  it("arms for the reviewed payload and never for a missing one", () => {
    expect(armedConfirmTransition(COMPOSE, { type: "arm", key: "A" })).toEqual(armedA);
    expect(armedConfirmTransition(COMPOSE, { type: "arm", key: null })).toBe(COMPOSE);
  });

  it("re-arming drops a prior acknowledgement", () => {
    const acked: ArmedConfirmState = { phase: "armed", key: "A", acknowledged: true };
    expect(armedConfirmTransition(acked, { type: "arm", key: "A" })).toEqual(armedA);
  });

  it("acknowledges only the payload that is armed", () => {
    expect(armedConfirmTransition(armedA, { type: "acknowledge", key: "A", value: true })).toEqual({
      ...armedA,
      acknowledged: true,
    });
    expect(armedConfirmTransition(armedA, { type: "acknowledge", key: "B", value: true })).toBe(armedA);
    expect(armedConfirmTransition(COMPOSE, { type: "acknowledge", key: "A", value: true })).toBe(COMPOSE);
  });

  it("a payload change disarms; the same payload keeps the state", () => {
    expect(armedConfirmTransition(armedA, { type: "sync", key: "B" })).toBe(COMPOSE);
    expect(armedConfirmTransition(armedA, { type: "sync", key: null })).toBe(COMPOSE);
    expect(armedConfirmTransition(armedA, { type: "sync", key: "A" })).toBe(armedA);
    expect(armedConfirmTransition(COMPOSE, { type: "sync", key: "B" })).toBe(COMPOSE);
  });

  it("disarm returns to compose", () => {
    expect(armedConfirmTransition(armedA, { type: "disarm" })).toBe(COMPOSE);
  });
});

describe("canonicalPayloadKey", () => {
  it("ignores key order and distinguishes values", () => {
    expect(canonicalPayloadKey({ a: 1, b: { c: 2, d: [1, 2] } })).toBe(
      canonicalPayloadKey({ b: { d: [1, 2], c: 2 }, a: 1 }),
    );
    expect(canonicalPayloadKey({ a: 1 })).not.toBe(canonicalPayloadKey({ a: 2 }));
    expect(canonicalPayloadKey({ url: "/api/orders/place" })).not.toBe(
      canonicalPayloadKey({ url: "/api/paper/place" }),
    );
  });
});

describe("useArmedConfirm", () => {
  function setup(initial: string | null) {
    return renderHook(({ k }: { k: string | null }) => useArmedConfirm(k), {
      initialProps: { k: initial },
    });
  }

  it("arms for the current payload", () => {
    const { result } = setup("A");
    expect(result.current.armed).toBe(false);
    act(() => result.current.arm());
    expect(result.current.armed).toBe(true);
    expect(result.current.acknowledged).toBe(false);
  });

  it("disarms the same render the payload changes, and stays disarmed if it changes back", () => {
    const { result, rerender } = setup("A");
    act(() => result.current.arm());
    act(() => result.current.setAcknowledged(true));
    expect(result.current.acknowledged).toBe(true);

    rerender({ k: "B" });
    expect(result.current.armed).toBe(false);
    expect(result.current.acknowledged).toBe(false);

    // A to B and back to A is not the reviewed order resurfacing: the operator
    // walked away from it, so it must be reviewed again.
    rerender({ k: "A" });
    expect(result.current.armed).toBe(false);
    expect(result.current.acknowledged).toBe(false);
  });

  it("cannot arm a payload that does not exist", () => {
    const { result } = setup(null);
    act(() => result.current.arm());
    expect(result.current.armed).toBe(false);
  });

  it("an acknowledgement does not survive re-arming", () => {
    const { result } = setup("A");
    act(() => result.current.arm());
    act(() => result.current.setAcknowledged(true));
    act(() => result.current.disarm());
    act(() => result.current.arm());
    expect(result.current.armed).toBe(true);
    expect(result.current.acknowledged).toBe(false);
  });
});
