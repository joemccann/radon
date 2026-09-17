"use client";

import { userErrorMessage } from "@/lib/userError";
import ErrorToast from "@/components/ErrorToast";

/** Shared safe error copy, retained-data context and recovery in the toast stack. */
export default function RequestError({ error, fallback, onRetry, retainedData = false, testId }: {
  error: unknown; fallback?: string; onRetry?: () => void; retainedData?: boolean; testId?: string;
}) {
  if (!error) return null;
  return <ErrorToast testId={testId} onRetry={onRetry} message={<>
    <div>{userErrorMessage(error, fallback)}</div>
    {retainedData ? <div>Showing the last available data. It has not been refreshed.</div> : null}
  </>} />;
}
