import { CopyAgentPrompt } from "@/components/molecules/CopyAgentPrompt";
import { getCapabilityPrompt } from "@/lib/agent-prompts";
import type { Gate } from "@/lib/editorial-content";

type GateCardProps = {
  gate: Gate;
  capabilityId?: string;
};

export function GateCard({ gate, capabilityId }: GateCardProps) {
  return (
    <div
      id={capabilityId}
      className={["gate", gate.disabled ? "gate-disabled" : ""].filter(Boolean).join(" ")}
    >
      <span className="font-mono text-[11px] uppercase tracking-[0.14em] text-signal-deep">
        {gate.no}
      </span>
      <h3 className="mb-2 mt-3 font-serif text-[1.28rem] font-medium text-primary">
        {gate.name}
      </h3>
      <p className="mb-3 text-[0.95rem] leading-[1.46] text-secondary">{gate.body}</p>
      <span className="gate-rule">{gate.rule}</span>
      {capabilityId ? (
        <div className="mt-4">
          <CopyAgentPrompt prompt={getCapabilityPrompt(capabilityId)} />
        </div>
      ) : null}
    </div>
  );
}
