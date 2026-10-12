"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import ErrorToast from "@/components/ErrorToast";

export type ServiceLogEntry = { ts: string; priority: number | null; message: string };

export type ServiceLogsResponse = {
  service: string | null;
  unit: string | null;
  available: boolean;
  reason: string | null;
  entries: ServiceLogEntry[];
  truncated: boolean;
};

type ServiceLogDisclosureProps = {
  service?: string;
  unit?: string;
  lastError?: string | null;
  lastRunIso?: string | null;
};

type LoadState =
  | { status: "loading" }
  | { status: "error" }
  | { status: "done"; data: ServiceLogsResponse };

/** journald priority: 0-3 error, 4 warning, 5 notice, 6 info, 7 debug. */
function levelOf(priority: number | null): { label: string; tone: "negative" | "warning" | "neutral" } {
  if (priority == null) return { label: "LOG", tone: "neutral" };
  if (priority <= 3) return { label: "ERROR", tone: "negative" };
  if (priority === 4) return { label: "WARN", tone: "warning" };
  if (priority === 5) return { label: "NOTICE", tone: "neutral" };
  if (priority === 6) return { label: "INFO", tone: "neutral" };
  return { label: "DEBUG", tone: "neutral" };
}

const isWarnOrWorse = (e: ServiceLogEntry) => e.priority != null && e.priority <= 4;

/** Deterministic UTC "MM-DD HH:MM:SS" so the list never depends on locale. */
function shortTs(iso: string): string {
  return /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}/.test(iso) ? `${iso.slice(5, 10)} ${iso.slice(11, 19)}` : iso;
}

/**
 * Journal lines for the systemd unit behind a failing writer or service row.
 * Mounted only when its row is expanded, so the fetch fires on disclosure.
 */
export default function ServiceLogDisclosure({ service, unit, lastError, lastRunIso }: ServiceLogDisclosureProps) {
  const [state, setState] = useState<LoadState>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);
  const [errorsOnly, setErrorsOnly] = useState<boolean | null>(null);

  const url = service
    ? `/api/admin/logs?service=${encodeURIComponent(service)}`
    : `/api/admin/logs?unit=${encodeURIComponent(unit ?? "")}`;

  useEffect(() => {
    const controller = new AbortController();
    setState({ status: "loading" });
    (async () => {
      try {
        const res = await fetch(url, { method: "GET", cache: "no-store", signal: controller.signal });
        if (!res.ok) throw new Error(`logs ${res.status}`);
        const data = (await res.json()) as ServiceLogsResponse;
        if (!controller.signal.aborted) setState({ status: "done", data });
      } catch {
        if (!controller.signal.aborted) setState({ status: "error" });
      }
    })();
    return () => controller.abort();
  }, [url, attempt]);

  const retry = useCallback(() => setAttempt((n) => n + 1), []);

  const entries = useMemo(() => (state.status === "done" ? state.data.entries ?? [] : []), [state]);
  const hasWarnings = entries.some(isWarnOrWorse);
  const filterOn = errorsOnly ?? hasWarnings;
  const visible = filterOn ? entries.filter(isWarnOrWorse) : entries;
  const runStart = lastRunIso ? Date.parse(lastRunIso) : Number.NaN;
  const unitLabel = (state.status === "done" ? state.data.unit : null) ?? unit ?? null;

  return (
    <div className="admin-journal-disclosure" data-testid="service-log-disclosure">
      <div className="admin-journal-header">
        <span className="admin-journal-title">Logs</span>
        {unitLabel && <span className="admin-journal-unit">{unitLabel}</span>}
        {state.status === "done" && state.data.available && entries.length > 0 && (
          <label className="admin-journal-filter">
            <input
              type="checkbox"
              checked={filterOn}
              onChange={(e) => setErrorsOnly(e.target.checked)}
            />
            Errors only
          </label>
        )}
      </div>

      {lastError && (
        <div className="admin-journal-raw">
          <span className="admin-journal-raw-label">Last error</span>
          <pre className="admin-journal-raw-error" data-testid="service-log-raw-error">{lastError}</pre>
        </div>
      )}

      {state.status === "loading" && (
        <div className="admin-journal-loading" data-testid="service-log-loading" aria-busy="true">
          {[80, 64, 72].map((w, i) => (
            <span key={i} className="admin-skeleton admin-skeleton-line" style={{ width: `${w}%` }} />
          ))}
        </div>
      )}

      {state.status === "error" && (
        <ErrorToast message="Logs could not be loaded. Try again." onRetry={retry} testId="service-log-error" />
      )}

      {state.status === "done" && !state.data.available && (
        <p className="admin-journal-empty">{state.data.reason || "Logs are not available on this host."}</p>
      )}

      {state.status === "done" && state.data.available && (
        entries.length === 0 ? (
          <p className="admin-journal-empty">No journal lines for this unit.</p>
        ) : visible.length === 0 ? (
          <p className="admin-journal-empty">No warnings or errors in the last {entries.length} lines.</p>
        ) : (
          <>
            <ol className="admin-journal-list">
              {visible.map((e, i) => {
                const level = levelOf(e.priority);
                const ts = Date.parse(e.ts);
                const thisRun = !Number.isNaN(runStart) && !Number.isNaN(ts) && ts >= runStart;
                return (
                  <li
                    key={`${e.ts}-${i}`}
                    className={`admin-journal-entry admin-journal-entry-${level.tone}${thisRun ? " admin-journal-entry-current" : ""}`}
                  >
                    <time className="admin-journal-ts" dateTime={e.ts} title={e.ts}>{shortTs(e.ts)}</time>
                    <span className="admin-journal-level">{level.label}</span>
                    <span className="admin-journal-message">{e.message}</span>
                    {thisRun && <span className="admin-journal-run">this run</span>}
                  </li>
                );
              })}
            </ol>
            {state.data.truncated && (
              <p className="admin-journal-note">Showing the most recent {entries.length} lines.</p>
            )}
          </>
        )
      )}
    </div>
  );
}
