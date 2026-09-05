"use client";

import type { ReactNode } from "react";
import { BorderBeam } from "border-beam";
import { HERO_BEAM } from "@/lib/librariesFx";
import { useSiteFxTheme } from "./useSiteFxTheme";

export function HeroBeam({ children }: { children: ReactNode }) {
  const theme = useSiteFxTheme();
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
