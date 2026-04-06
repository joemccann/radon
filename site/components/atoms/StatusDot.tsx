type StatusTone =
  | "core"
  | "strong"
  | "warn"
  | "fault"
  | "clear"
  | "emerging"
  | "dislocated"
  | "muted";

type StatusDotProps = {
  tone?: StatusTone;
  pulse?: boolean;
  className?: string;
};

const toneClass: Record<StatusTone, string> = {
  core: "bg-accent border-accent/30",
  strong: "bg-signal-strong border-signal-strong/30",
  warn: "bg-warn border-warn/30",
  fault: "bg-negative border-negative/30",
  clear: "bg-accent border-accent/30",
  emerging: "bg-signal-deep border-signal-deep/30",
  dislocated: "bg-dislocation border-dislocation/30",
  muted: "bg-secondary border-secondary",
};

export function StatusDot({
  tone = "core",
  pulse = false,
  className,
}: StatusDotProps) {
  return (
    <span
      aria-hidden="true"
      className={[
        "inline-flex h-2.5 w-2.5 rounded-full border",
        toneClass[tone],
        pulse ? "animate-pulse" : "",
        className,
      ]
        .filter(Boolean)
        .join(" ")}
    />
  );
}
