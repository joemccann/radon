import { SignalPill } from "@/components/atoms/SignalPill";
import { TelemetryLabel } from "@/components/atoms/TelemetryLabel";
import { StrategyGrid } from "@/components/organisms/StrategyGrid";

export function StrategySection() {
  return (
    <section id="strategies" className="border-t border-grid py-16 md:py-24">
      <div className="flex flex-col gap-6 border-b border-grid pb-8 md:flex-row md:items-end md:justify-between">
        <div className="max-w-3xl">
          <TelemetryLabel tone="core">Strategy Matrix</TelemetryLabel>
          <h2 className="mt-4 font-display text-4xl font-semibold text-primary md:text-5xl">
            Real strategy modules, not generic feature cards.
          </h2>
          <p className="mt-5 text-base leading-7 text-secondary">
            Each module exposes its edge source, instruments, hold period, expected
            behavior, and command surface so you understand what is deployable and why.
          </p>
        </div>
        <div className="flex flex-wrap gap-3">
          <SignalPill tone="core">Defined Risk</SignalPill>
          <SignalPill tone="warn">Managed Undefined Risk</SignalPill>
          <SignalPill tone="violet">Overlay / Hedge</SignalPill>
        </div>
      </div>
      <div className="mt-8">
        <StrategyGrid />
      </div>
    </section>
  );
}
