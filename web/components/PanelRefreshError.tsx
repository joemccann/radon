"use client";

import RequestError from "@/components/RequestError";

/** A failed refresh must disclose that retained measurements are stale. */
export default function PanelRefreshError({ error, testId }: {
  error: string | null | undefined;
  testId?: string;
}) {
  if (!error) return null;
  return <RequestError error={error} retainedData testId={testId ?? "panel-refresh-error"} />;
}
