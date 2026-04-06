import type { ReactNode } from "react";

type SignalTone =
  | "core"
  | "strong"
  | "warn"
  | "violet"
  | "neutral"
  | "clear"
  | "emerging"
  | "dislocated"
  | "muted";

type SignalPillProps = {
  children: ReactNode;
  tone?: SignalTone;
  className?: string;
};

const toneClass: Record<SignalTone, string> = {
  core: "border-accent/40 text-accent",
  strong: "border-signal-strong/40 text-signal-strong",
  warn: "border-warn/40 text-warn",
  violet: "border-extreme/40 text-extreme",
  neutral: "border-grid text-secondary",
  clear: "border-accent/40 text-accent",
  emerging: "border-signal-deep/40 text-signal-deep",
  dislocated: "border-dislocation/40 text-dislocation",
  muted: "border-grid text-secondary",
};

export function SignalPill({
  children,
  tone = "neutral",
  className,
}: SignalPillProps) {
  return (
    <span
      className={[
        "inline-flex items-center gap-2 rounded-full border px-3 py-2 sm:py-1 font-mono text-[11px] uppercase tracking-[0.16em]",
        toneClass[tone],
        className,
      ]
        .filter(Boolean)
        .join(" ")}
    >
      {children}
    </span>
  );
}
