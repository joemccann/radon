import type { ToneType } from "@/lib/tone";

type EdgeTraceProps = {
  tone?: ToneType;
  className?: string;
};

const toneClass: Record<ToneType, string> = {
  core: "bg-accent",
  warn: "bg-warn",
  violet: "bg-dislocation",
  clear: "bg-accent",
  strong: "bg-signal-strong",
  emerging: "bg-signal-deep",
  dislocated: "bg-dislocation",
  fault: "bg-negative",
  neutral: "bg-secondary/60",
  muted: "bg-secondary/60",
};

export function EdgeTrace({ tone = "core", className }: EdgeTraceProps) {
  return (
    <span
      aria-hidden="true"
      className={[
        "absolute inset-y-0 left-0 w-px",
        toneClass[tone],
        className,
      ]
        .filter(Boolean)
        .join(" ")}
    />
  );
}
