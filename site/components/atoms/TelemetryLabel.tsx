import type { ReactNode } from "react";
import type { ToneType } from "@/lib/tone";

type TelemetryTone = ToneType | "primary";

type TelemetryLabelProps = {
  children: ReactNode;
  tone?: TelemetryTone;
  className?: string;
};

const toneClass: Record<TelemetryTone, string> = {
  primary: "text-primary",
  muted: "text-muted",
  core: "text-accent",
  warn: "text-warn",
  strong: "text-signal-strong",
  fault: "text-negative",
  violet: "text-extreme",
  neutral: "text-secondary",
  clear: "text-accent",
  emerging: "text-accent",
  dislocated: "text-dislocation",
};

export function TelemetryLabel({
  children,
  tone = "muted",
  className,
}: TelemetryLabelProps) {
  return (
    <div
      className={[
        "font-mono text-[11px] uppercase tracking-[0.2em]",
        toneClass[tone],
        className,
      ]
        .filter(Boolean)
        .join(" ")}
    >
      {children}
    </div>
  );
}
