import { CommandChip } from "@/components/atoms/CommandChip";
import { SignalPill } from "@/components/atoms/SignalPill";
import { TelemetryLabel } from "@/components/atoms/TelemetryLabel";

export function FinalCTASection() {
  return (
    <section className="py-16 md:py-24">
      <div className="relative overflow-hidden border border-accent/30 bg-panel-raised px-6 py-8 md:px-8 md:py-10">
        <div className="absolute inset-0 projection-lines opacity-[0.04]" />
        <div className="relative z-10">
        <TelemetryLabel tone="core">Operator Handoff</TelemetryLabel>
        <div className="mt-5 grid gap-8 xl:grid-cols-[minmax(0,1fr)_auto] xl:items-end">
          <div>
            <h2 className="max-w-[16ch] font-display text-4xl font-semibold leading-tight text-primary md:text-5xl">
              Inspect the strategy stack before the market forces the question.
            </h2>
            <p className="mt-5 max-w-2xl text-base leading-7 text-secondary">
              The right conversion target is not hype. It is informed curiosity from a
              serious operator who wants to understand the machine and then use it.
            </p>
            <div className="mt-6 flex flex-wrap gap-3">
              <SignalPill tone="core">Explainable</SignalPill>
              <SignalPill tone="strong">Execution Aware</SignalPill>
              <SignalPill tone="neutral">Open Source</SignalPill>
            </div>
          </div>
          <div className="flex flex-wrap gap-3 xl:justify-end">
            <a
              href="#strategies"
              className="inline-flex items-center border border-accent bg-accent px-6 py-3.5 font-mono text-[11px] uppercase tracking-[0.16em] text-canvas transition-colors hover:bg-signal-strong"
            >
              Review Strategies
            </a>
            <a
              href="https://github.com/joemccann/radon"
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex"
            >
              <CommandChip command="Explore Source" />
            </a>
          </div>
        </div>
        </div>
      </div>
    </section>
  );
}
