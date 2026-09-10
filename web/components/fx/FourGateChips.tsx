"use client";

import { FOUR_GATES, gateBeamActive, type GateChipState, type GateId } from "@/lib/librariesFx";
import EvaluatingBeam from "./EvaluatingBeam";

type FourGateChipsProps = {
  states: Record<GateId, GateChipState>;
};

export function FourGateChips({ states }: FourGateChipsProps) {
  return (
    <div className="four-gate-chips" data-testid="four-gate-chips">
      {FOUR_GATES.map((gate) => {
        const state = states[gate.id];
        return (
          <EvaluatingBeam key={gate.id} active={gateBeamActive(state)}>
            <span
              className={`gate-chip gate-chip--${state}`}
              data-testid={`gate-chip-${gate.id}`}
              data-gate-state={state}
            >
              Gate {gate.id}
              <span className="gate-chip__name">{gate.name}</span>
            </span>
          </EvaluatingBeam>
        );
      })}
    </div>
  );
}
