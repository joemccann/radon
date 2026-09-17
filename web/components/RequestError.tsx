"use client";

import { AlertTriangle } from "lucide-react";
import { userErrorMessage } from "@/lib/userError";

/** Existing inline alert styling, with shared safe copy and optional recovery. */
export default function RequestError({ error, fallback, onRetry, retainedData = false, testId }: {
  error: unknown; fallback?: string; onRetry?: () => void; retainedData?: boolean; testId?: string;
}) {
  if (!error) return null;
  return <div className="alert-item bearish" role="alert" data-testid={testId} style={{ display: "flex", alignItems: "flex-start", gap: 8, flexWrap: "wrap", overflowWrap: "anywhere" }}>
    <AlertTriangle size={16} aria-hidden="true" style={{ flexShrink: 0 }} />
    <div style={{ flex: "1 1 220px", minWidth: 0 }}>
      <div>{userErrorMessage(error, fallback)}</div>
      {retainedData ? <div>Showing the last available data. It has not been refreshed.</div> : null}
    </div>
    {onRetry ? <button type="button" className="btn-secondary" onClick={onRetry}>Try again</button> : null}
  </div>;
}
