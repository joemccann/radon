/**
 * Matte instrument-module loading shell (skill-stack T6).
 * Hairline panel + muted rails — no glass, no soft shadows.
 */
export default function InstrumentSkeleton({
  testId = "instrument-skeleton",
}: {
  testId?: string;
}) {
  return (
    <div className="instrument-skeleton" data-testid={testId} aria-busy="true" aria-label="Loading module">
      <div className="instrument-skeleton__header">
        <div className="instrument-skeleton__bar instrument-skeleton__bar--eyebrow" />
        <div className="instrument-skeleton__bar instrument-skeleton__bar--title" />
      </div>
      <div className="instrument-skeleton__bar instrument-skeleton__bar--row" />
      <div className="instrument-skeleton__bar instrument-skeleton__bar--row" style={{ width: "88%" }} />
      <div className="instrument-skeleton__bar instrument-skeleton__bar--row" style={{ width: "72%" }} />
      <div className="instrument-skeleton__rail" aria-hidden>
        <div className="instrument-skeleton__bar instrument-skeleton__bar--meta" />
        <div className="instrument-skeleton__bar instrument-skeleton__bar--meta" />
        <div className="instrument-skeleton__bar instrument-skeleton__bar--meta" />
      </div>
    </div>
  );
}
