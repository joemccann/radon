"use client";

import { Component, type ReactNode } from "react";

/** Preserve the label when a decorative renderer is unavailable. */
export class MarkerFallback extends Component<{ children: ReactNode; fallback: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() { return this.state.failed ? this.props.fallback : this.props.children; }
}
