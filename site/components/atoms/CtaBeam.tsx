"use client";

import type { ReactNode } from "react";
import { BorderBeam } from "border-beam";
import { CTA_BEAM } from "@/lib/librariesFx";
import { useSiteFxTheme } from "./useSiteFxTheme";

export function CtaBeam({ children }: { children: ReactNode }) {
  const theme = useSiteFxTheme();
  return (
    <BorderBeam
      {...CTA_BEAM}
      theme={theme}
      className="cta-beam"
      data-testid="cta-beam"
    >
      {children}
    </BorderBeam>
  );
}
