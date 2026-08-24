"use client";

import { AlertTriangle } from "lucide-react";

/**
 * R-124: `useSyncHook` keeps the previous `data` when a refresh fails, so a
 * panel that only guards on `error && !data` renders the last good numbers
 * as if they were current. This is the affordance for the other case — the
 * chart still shows what it has, with the failure stated next to it.
 */
export default function PanelRefreshError({
  error,
  testId,
}: {
  error: string | null | undefined;
  testId?: string;
}) {
  if (!error) return null;
  return (
    <span
      data-testid={testId ?? "panel-refresh-error"}
      title={error}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: "4px",
        fontFamily: "var(--font-mono)",
        fontSize: "9px",
        color: "var(--fault)",
      }}
    >
      <AlertTriangle size={10} />
      REFRESH FAILED
    </span>
  );
}
