import { TelemetryLabel } from "@/components/atoms/TelemetryLabel";
import { ExecutionWorkflow } from "@/components/organisms/ExecutionWorkflow";

export function ExecutionSection() {
  return (
    <section id="execution" className="border-t border-grid py-16 md:py-24">
      <div className="max-w-3xl">
        <TelemetryLabel tone="core">Execution Rail</TelemetryLabel>
        <h2 className="mt-4 font-display text-4xl font-semibold text-primary md:text-5xl">
          Signal is only the start. Execution has to survive contact with risk.
        </h2>
        <p className="mt-5 text-base leading-7 text-secondary">
          Radon connects candidate selection to structure design, bankroll sizing,
          execution, and post-trade measurement. The message is simple: no hidden
          break between conviction and capital deployment.
        </p>
      </div>
      <div className="mt-8">
        <ExecutionWorkflow />
      </div>
    </section>
  );
}
