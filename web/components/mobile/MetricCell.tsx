type Tone = "pos" | "neg" | "warn" | "mut";
type Size = "hero" | "primary" | "secondary";

type MetricCellProps = {
  label: string;
  value: string;
  tone?: Tone;
  size?: Size;
  title?: string;
};

export function MetricCell({ label, value, tone, size = "primary", title }: MetricCellProps) {
  const toneClass = tone ? ` m-metric__value--${tone}` : "";
  const sizeClass = ` m-metric__value--${size}`;

  return (
    <div className="m-metric" title={title}>
      <span className="m-metric__label">{label}</span>
      <span className={`m-metric__value${sizeClass}${toneClass}`}>{value}</span>
    </div>
  );
}

export default MetricCell;
