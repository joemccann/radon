import { TelemetryLabel } from "@/components/atoms/TelemetryLabel";
import type { ExecutionItem } from "@/lib/landing-content";

type LegacyExecution = {
  id?: string | number;
  title?: string;
  step?: string;
  summary?: string;
  source?: string;
  latency?: string;
  metadata?: string;
};

type ExecutionStepProps = {
  item?: ExecutionItem;
  index?: number;
  step?: LegacyExecution;
  terminal?: boolean;
};

export function ExecutionStep({ item, index, step }: ExecutionStepProps) {
  const normalizedItem: ExecutionItem = item ?? {
    step: step?.title ?? step?.step ?? "Step",
    summary: step?.summary ?? "",
    metadata: step?.metadata ?? [step?.source, step?.latency].filter(Boolean).join(" / "),
  };

  const normalizedIndex = index ?? Number(step?.id ?? 1) - 1;

  return (
    <article className="relative border border-grid bg-panel px-5 py-5">
      <div className="flex items-start justify-between gap-4">
        <div>
          <TelemetryLabel tone="core">Step {normalizedIndex + 1}</TelemetryLabel>
          <h3 className="mt-3 font-sans text-xl font-semibold text-primary">
            {normalizedItem.step}
          </h3>
        </div>
        <span className="font-mono text-[11px] uppercase tracking-[0.16em] text-muted">
          Operator Path
        </span>
      </div>
      <p className="mt-4 text-sm leading-6 text-secondary">{normalizedItem.summary}</p>
      <div className="mt-6 border-t border-grid pt-4">
        <TelemetryLabel>Audit Trail</TelemetryLabel>
        <p className="mt-2 font-mono text-[12px] uppercase tracking-[0.14em] text-secondary">
          {normalizedItem.metadata}
        </p>
      </div>
    </article>
  );
}
