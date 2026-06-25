import type { Milestone } from "@/lib/editorial-content";

type MilestoneRowProps = {
  milestone: Milestone;
};

// The leading "01..07" is rendered by the CSS counter on `.milestone-no`
// (globals.css), so the order is structural rather than hardcoded per row.
export function MilestoneRow({ milestone }: MilestoneRowProps) {
  return (
    <div className="grid grid-cols-[40px_1fr] items-start gap-[18px] border-b border-hairline-soft py-[26px] sm:grid-cols-[56px_1fr] sm:gap-7">
      <span
        aria-hidden="true"
        className="milestone-no pt-[5px] font-mono text-[13px] font-semibold text-signal-deep"
      />
      <div>
        <h3 className="mb-[7px] font-serif text-[1.32rem] font-medium tracking-[-0.01em] text-primary">
          {milestone.name}
        </h3>
        <p className="m-0 max-w-[62ch] text-[1.02rem] leading-[1.5] text-secondary">
          {milestone.body}
        </p>
        <span className="mt-2 inline-block font-mono text-[10.5px] uppercase tracking-[0.06em] text-muted">
          {milestone.tag}
        </span>
      </div>
    </div>
  );
}
