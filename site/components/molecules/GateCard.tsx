import { MarkerAccent } from "@/components/atoms/MarkerAccent";
import type { Gate } from "@/lib/editorial-content";

type GateCardProps = {
  gate: Gate;
};

export function GateCard({ gate }: GateCardProps) {
  return (
    <div className={["gate", gate.disabled ? "gate-disabled" : ""].filter(Boolean).join(" ")}>
      <MarkerAccent>
        <span className="inline-block font-mono text-[11px] uppercase tracking-[0.14em] text-signal-deep">
          {gate.no}
        </span>
      </MarkerAccent>
      <h3 className="mb-2 mt-3 font-serif text-[1.28rem] font-medium text-primary">
        {gate.name}
      </h3>
      <p className="mb-3 text-[0.95rem] leading-[1.46] text-secondary">{gate.body}</p>
      <span className="gate-rule">{gate.rule}</span>
    </div>
  );
}
