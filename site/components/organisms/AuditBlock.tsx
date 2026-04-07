import { SignalPill } from "@/components/atoms/SignalPill";
import { TelemetryLabel } from "@/components/atoms/TelemetryLabel";
import { auditItems } from "@/lib/landing-content";

export function AuditBlock() {
  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)]">
      <div className="relative overflow-hidden border border-grid bg-panel px-6 py-6">
        <div className="absolute inset-0 projection-lines opacity-[0.08]" />
        <div className="relative z-20">
          <TelemetryLabel tone="core">Auditability Layer</TelemetryLabel>
          <h3 className="mt-4 max-w-[18ch] font-sans text-3xl font-semibold leading-tight text-primary md:text-4xl">
            Open architecture for traders who want to inspect the machine.
          </h3>
          <p className="mt-5 max-w-2xl text-base leading-7 text-secondary">
            Radon should feel like a calibrated instrument, not a black box asking
            for trust. Methodology, source chain, and explainability remain visible
            so the operator can decide how much confidence the system deserves.
          </p>
          <div className="mt-6 flex flex-wrap gap-3">
            <SignalPill tone="core">Open Source</SignalPill>
            <SignalPill tone="strong">Explainable Metrics</SignalPill>
            <SignalPill tone="neutral">Operator Controlled</SignalPill>
          </div>
        </div>
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        {auditItems.map((item) => (
          <article key={item.title} className="border border-grid bg-panel px-5 py-5 transition-colors duration-200 hover:border-accent/40">
            <TelemetryLabel>{item.title}</TelemetryLabel>
            <p className="mt-4 text-sm leading-6 text-primary">{item.detail}</p>
          </article>
        ))}
      </div>
    </div>
  );
}
