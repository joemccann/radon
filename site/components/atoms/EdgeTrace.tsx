type EdgeTone =
  | "core"
  | "warn"
  | "violet"
  | "clear"
  | "strong"
  | "emerging"
  | "dislocated"
  | "muted";

type EdgeTraceProps = {
  tone?: EdgeTone;
  className?: string;
};

const toneClass: Record<EdgeTone, string> = {
  core: "bg-accent",
  warn: "bg-warn",
  violet: "bg-dislocation",
  clear: "bg-accent",
  strong: "bg-signal-strong",
  emerging: "bg-signal-deep",
  dislocated: "bg-dislocation",
  muted: "bg-secondary/60",
};

export function EdgeTrace({ tone = "core", className }: EdgeTraceProps) {
  return (
    <span
      aria-hidden="true"
      className={[
        "absolute inset-y-3 left-0 w-px",
        toneClass[tone],
        className,
      ]
        .filter(Boolean)
        .join(" ")}
    />
  );
}
