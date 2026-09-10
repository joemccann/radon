"use client";

import type { ReactNode } from "react";
import { BorderBeam } from "border-beam";
import { GATE_BEAM } from "@/lib/librariesFx";
import { useDataTheme } from "./useDataTheme";

type EvaluatingBeamProps = {
  active: boolean;
  children: ReactNode;
  className?: string;
};

export default function EvaluatingBeam({ active, children, className }: EvaluatingBeamProps) {
  const theme = useDataTheme();
  if (!active) return children;
  return (
    <BorderBeam
      {...GATE_BEAM}
      active
      theme={theme}
      className={className}
      data-testid="evaluating-beam"
      data-beam-active="true"
    >
      {children}
    </BorderBeam>
  );
}
