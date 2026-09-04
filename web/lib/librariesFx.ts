import type { IBDisplayStatus } from "./IBStatusContext";

export type ThinkingWaitKind = "flow" | "gex" | "evaluate" | "agent" | "compute";

export const THINKING_ORB_STATE = {
  flow: "searching",
  gex: "weaving",
  evaluate: "solving",
  agent: "working",
  compute: "composing",
} as const;

export type ThinkingOrbState = (typeof THINKING_ORB_STATE)[ThinkingWaitKind];

export function thinkingOrbState(kind: ThinkingWaitKind): ThinkingOrbState {
  return THINKING_ORB_STATE[kind];
}

export type GateChipState = "idle" | "evaluating" | "cleared" | "failed";

export const FOUR_GATES = [
  { id: "01", name: "Convexity" },
  { id: "02", name: "Edge" },
  { id: "03", name: "Risk" },
  { id: "04", name: "Naked Shorts" },
] as const;

export type GateId = (typeof FOUR_GATES)[number]["id"];

export function gateBeamActive(state: GateChipState): boolean {
  return state === "evaluating";
}

export function ibStatusBeamActive(status: IBDisplayStatus): boolean {
  return status === "connected";
}

export const GATE_BEAM = {
  size: "sm" as const,
  colorVariant: "mono" as const,
  staticColors: true,
  strength: 0.55,
  borderRadius: 4,
};
