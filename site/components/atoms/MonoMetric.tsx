import { TelemetryLabel } from "@/components/atoms/TelemetryLabel";
import type { ToneType } from "@/lib/tone";

type MonoTone = ToneType | "primary";
type MonoMetricSize = "default" | "compact";

type MonoMetricProps = {
  value: string;
  suffix?: string;
  className?: string;
  label?: string;
  detail?: string;
  tone?: MonoTone;
  size?: MonoMetricSize;
};

const toneClass: Record<MonoTone, string> = {
  primary: "text-primary",
  core: "text-accent",
  strong: "text-signal-strong",
  warn: "text-warn",
  fault: "text-negative",
  violet: "text-extreme",
  neutral: "text-secondary",
  clear: "text-accent",
  emerging: "text-accent",
  dislocated: "text-dislocation",
  muted: "text-muted",
};

export function MonoMetric({
  label,
  value,
  detail,
  suffix,
  tone = "primary",
  size = "default",
  className,
}: MonoMetricProps) {
  const valueClass =
    size === "compact"
      ? "mt-3 max-w-full break-words font-mono text-[clamp(1.375rem,1.35vw,1.5rem)] leading-[1.08]"
      : "mt-3 font-mono text-[28px] leading-[1.05]";

  if (label || detail) {
    return (
      <div className={["min-w-0 border border-grid bg-panel px-4 py-4", className].filter(Boolean).join(" ")}>
        {label ? <TelemetryLabel>{label}</TelemetryLabel> : null}
        <div className={[valueClass, toneClass[tone]].join(" ")}>
          <span className="mono-metric-value">{value}</span>
        </div>
        {detail ? (
          <p className="mt-2 font-mono text-[11px] uppercase tracking-[0.16em] text-secondary">
            {detail}
          </p>
        ) : null}
      </div>
    );
  }

  return (
    <div
      className={[
        "font-mono text-[28px] leading-[1.05] text-primary",
        className,
      ]
        .filter(Boolean)
        .join(" ")}
    >
      {value}
      {suffix ? <span className="ml-1 text-[14px] text-secondary">{suffix}</span> : null}
    </div>
  );
}
