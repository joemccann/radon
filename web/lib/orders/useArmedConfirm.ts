"use client";

import { useCallback, useEffect, useReducer } from "react";

/**
 * Armed-for-payload confirm machine shared by the order tickets.
 *
 * A ticket's confirm step used to be a boolean: it stayed true while the
 * price, legs, TIF or destination under it changed, so the operator reviewed
 * one order and transmitted another. Arming here captures a canonical key of
 * the reviewed payload. The ticket is armed only while its current payload
 * key equals that key; any change disarms, and the unbounded-loss
 * acknowledgement belongs to the armed payload alone.
 */
export type ArmedConfirmState =
  | { phase: "compose" }
  | { phase: "armed"; key: string; acknowledged: boolean };

export type ArmedConfirmEvent =
  | { type: "arm"; key: string | null }
  | { type: "disarm" }
  | { type: "acknowledge"; key: string | null; value: boolean }
  | { type: "sync"; key: string | null };

export const COMPOSE: ArmedConfirmState = { phase: "compose" };

export function armedConfirmTransition(
  state: ArmedConfirmState,
  event: ArmedConfirmEvent,
): ArmedConfirmState {
  switch (event.type) {
    case "arm":
      return event.key == null ? COMPOSE : { phase: "armed", key: event.key, acknowledged: false };
    case "disarm":
      return COMPOSE;
    case "acknowledge":
      if (state.phase !== "armed" || state.key !== event.key) return state;
      if (state.acknowledged === event.value) return state;
      return { ...state, acknowledged: event.value };
    case "sync":
      if (state.phase === "armed" && state.key !== event.key) return COMPOSE;
      return state;
  }
}

function canonicalize(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonicalize);
  if (value != null && typeof value === "object") {
    const out: Record<string, unknown> = {};
    for (const k of Object.keys(value as Record<string, unknown>).sort()) {
      const v = (value as Record<string, unknown>)[k];
      if (v !== undefined) out[k] = canonicalize(v);
    }
    return out;
  }
  return value;
}

/** Key-order independent serialisation of a reviewed order payload. */
export function canonicalPayloadKey(payload: unknown): string {
  return JSON.stringify(canonicalize(payload));
}

export type ArmedConfirm = {
  /** True only while the armed key equals the current payload key. */
  armed: boolean;
  /** Unbounded-loss acknowledgement for the armed payload. */
  acknowledged: boolean;
  arm: () => void;
  disarm: () => void;
  setAcknowledged: (value: boolean) => void;
};

/**
 * `payloadKey` is the canonical key of what transmit would send right now
 * (null when the ticket cannot form an order). Armed is derived against it on
 * every render, so a changed payload reads disarmed in the same render it
 * changes; the effect then drops the armed state so changing back does not
 * silently re-arm.
 */
export function useArmedConfirm(payloadKey: string | null): ArmedConfirm {
  const [state, dispatch] = useReducer(armedConfirmTransition, COMPOSE);

  useEffect(() => {
    dispatch({ type: "sync", key: payloadKey });
  }, [payloadKey]);

  const armed = state.phase === "armed" && state.key === payloadKey;
  const acknowledged = armed && state.phase === "armed" && state.acknowledged;

  const arm = useCallback(() => dispatch({ type: "arm", key: payloadKey }), [payloadKey]);
  const disarm = useCallback(() => dispatch({ type: "disarm" }), []);
  const setAcknowledged = useCallback(
    (value: boolean) => dispatch({ type: "acknowledge", key: payloadKey, value }),
    [payloadKey],
  );

  return { armed, acknowledged, arm, disarm, setAcknowledged };
}
