"use client";

import { userErrorMessage } from "@/lib/userError";
import type { RestartLogEntry } from "@/lib/adminTypes";

type RestartLogProps = {
  entries: RestartLogEntry[];
};

/**
 * Client-side log of recent operator actions. There is no persistent backend
 * log yet (flagged as a follow-up), so this lives in component state and
 * resets on page reload. Limit kept tight (5) so the panel stays scannable.
 */
export default function RestartLog({ entries }: RestartLogProps) {
  if (entries.length === 0) {
    return (
      <section className="admin-card" data-testid="restart-log">
        <header className="admin-card-header">
          <span className="admin-card-title">Recent actions</span>
        </header>
        <p className="admin-card-empty">
          No actions yet this session. Force pushes and service controls show
          up here after they run.
        </p>
      </section>
    );
  }

  return (
    <section className="admin-card" data-testid="restart-log">
      <header className="admin-card-header">
        <span className="admin-card-title">Recent actions</span>
        <span className="admin-card-note-inline">last 5, session only</span>
      </header>
      <ul className="admin-log-list">
        {entries.slice(0, 5).map((entry, idx) => (
          <li
            key={`${entry.at}-${idx}`}
            className={`admin-log-row ${entry.ok ? "admin-log-row-ok" : "admin-log-row-err"}`}
          >
            <span className="admin-log-time">{formatTime(entry.at)}</span>
            <span className="admin-log-action">{entry.action}</span>
            <span className="admin-log-target">{entry.target}</span>
            <span className="admin-log-detail">{entry.ok ? entry.detail : userErrorMessage(entry.detail, "The action could not be completed. Review service status and try again.")}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

function formatTime(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  } catch {
    return iso;
  }
}
