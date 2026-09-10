"use client";

import type { ReactNode } from "react";
import { BorderBeam } from "border-beam";
import { HERO_BEAM } from "@/lib/librariesFx";
import { usePrefersReducedMotion } from "./usePrefersReducedMotion";
import { useSiteFxTheme } from "./useSiteFxTheme";

export function HeroBeam({ children }: { children: ReactNode }) {
  const theme = useSiteFxTheme();
  const reduceMotion = usePrefersReducedMotion();
  if (reduceMotion) {
    return (
      <span className="hero-beam" data-testid="hero-beam">
        {children}
      </span>
    );
  }
  return (
    <BorderBeam
      {...HERO_BEAM}
      theme={theme}
      className="hero-beam"
      data-testid="hero-beam"
    >
      {children}
    </BorderBeam>
  );
}
