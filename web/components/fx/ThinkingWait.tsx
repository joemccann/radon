"use client";

import { ThinkingOrb } from "thinking-orbs";
import { thinkingOrbState, type ThinkingWaitKind } from "@/lib/librariesFx";

type ThinkingWaitProps = {
  kind: ThinkingWaitKind;
  label: string;
  size?: 20 | 64;
};

export default function ThinkingWait({ kind, label, size = 20 }: ThinkingWaitProps) {
  return (
    <span
      className="thinking-wait"
      data-testid="thinking-wait"
      data-kind={kind}
      role="status"
      aria-label={label}
    >
      <ThinkingOrb state={thinkingOrbState(kind)} size={size} theme="auto" aria-hidden />
    </span>
  );
}
