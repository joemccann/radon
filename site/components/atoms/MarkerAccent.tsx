"use client";

import type { ReactNode } from "react";
import { MetalFx } from "metal-fx";
import { MARKER_METAL } from "@/lib/librariesFx";
import { useSiteFxTheme } from "./useSiteFxTheme";

export function MarkerAccent({ children }: { children: ReactNode }) {
  const theme = useSiteFxTheme();
  return (
    <MetalFx {...MARKER_METAL} theme={theme} data-testid="marker-accent">
      {children}
    </MetalFx>
  );
}
