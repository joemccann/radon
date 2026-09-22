type SectionHeadingProps = {
  no: string;
  label: string;
};

export function SectionHeading({ no, label }: SectionHeadingProps) {
  return (
    <div className="reveal mb-9 flex flex-wrap items-baseline gap-3">
      <span className="text-[12px] font-semibold text-signal-deep">
        {no}
      </span>
      <span className="text-[12px] text-muted">
        {label}
      </span>
    </div>
  );
}
