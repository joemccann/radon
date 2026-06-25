type SectionHeadingProps = {
  no: string;
  label: string;
};

export function SectionHeading({ no, label }: SectionHeadingProps) {
  return (
    <div className="reveal mb-9 flex flex-wrap items-baseline gap-[18px]">
      <span className="font-mono text-[11.5px] uppercase tracking-[0.14em] text-muted">
        {no}
      </span>
      <span className="font-mono text-[11.5px] uppercase tracking-[0.18em] text-signal-deep">
        {label}
      </span>
    </div>
  );
}
