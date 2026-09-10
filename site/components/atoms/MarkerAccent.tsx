"use client";

import { Component, type ReactNode } from "react";
import { MetalFx } from "metal-fx";
import { MARKER_METAL } from "@/lib/librariesFx";
import { useSiteFxTheme } from "./useSiteFxTheme";

/** A decorative renderer must never take the marketing page down on GPU-less clients. */
class MarkerFallback extends Component<{ children: ReactNode; fallback: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() { return this.state.failed ? this.props.fallback : this.props.children; }
}

export function MarkerAccent({ children }: { children: ReactNode }) {
  const theme = useSiteFxTheme();
  return (
    <MarkerFallback fallback={<span data-testid="marker-accent">{children}</span>}>
      <MetalFx {...MARKER_METAL} theme={theme} data-testid="marker-accent">
        {children}
      </MetalFx>
    </MarkerFallback>
  );
}
