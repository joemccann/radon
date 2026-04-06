import { SignalPill } from "@/components/atoms/SignalPill";
import { TelemetryLabel } from "@/components/atoms/TelemetryLabel";
import { ExecutionStep } from "@/components/molecules/ExecutionStep";
import { executionItems } from "@/lib/landing-content";

export function ExecutionWorkflow() {
  return (
    <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_minmax(0,0.85fr)]">
      <div className="grid gap-4 lg:grid-cols-2 xl:grid-cols-3">
        {executionItems.map((item, index) => (
          <ExecutionStep key={item.step} item={item} index={index} />
        ))}
      </div>
      <aside className="border border-grid bg-panel-raised px-6 py-6">
        <TelemetryLabel tone="core">Execution Guarantees</TelemetryLabel>
        <h3 className="mt-3 font-sans text-xl font-semibold text-primary">
          No hidden handoff between signal and risk.
        </h3>
        <p className="mt-4 text-sm leading-6 text-secondary">
          Radon carries every position from candidate selection through structure
          design, bankroll sizing, execution, and post-trade auditability. Nothing
          falls through the gap between conviction and capital deployment.
        </p>
        <div className="mt-6 flex flex-wrap gap-3">
          <SignalPill tone="core">Source Linked</SignalPill>
          <SignalPill tone="strong">Kelly Bounded</SignalPill>
          <SignalPill tone="neutral">Portfolio Aware</SignalPill>
        </div>
        <div className="mt-6 border-t border-grid pt-5">
          <TelemetryLabel>Operator Notes</TelemetryLabel>
          <ul className="mt-4 space-y-3 text-sm leading-6 text-secondary">
            <li>Every step exposes methodology, not just an output state.</li>
            <li>Motion only explains progression and scan status.</li>
            <li>Defined-risk structures remain first-class citizens.</li>
            <li>Every short option must be fully covered. Naked calls, naked stock shorts, and partial coverage are blocked before the order reaches the exchange.</li>
          </ul>
        </div>
      </aside>
    </div>
  );
}
