import { existsSync } from "node:fs";
import { resolve } from "node:path";

const REPLICA_WATCHDOG_SERVICE = "replica-watchdog";

/** Turso row written by the disabled radon-research.service. */
export const RETIRED_RESEARCH_SERVICE = "dropbox-research";
export const RETIRED_RESEARCH_UNIT = "radon-research.service";
/**
 * Live research health. Neither service runs the ingestion review stage.
 * marketdesk-research publishes articles. marketdesk-pdf archives PDFs.
 */
export const LIVE_RESEARCH_SERVICES = ["marketdesk-research", "marketdesk-pdf"] as const;

/**
 * The watchdog only exists to protect the embedded replica. A retired replica
 * can leave a latest-state row and transition history in Turso indefinitely;
 * those records stop being applicable when the replica file is absent.
 */
export function replicaDatabaseExists(): boolean {
  const replicaPath = resolve(
    /* turbopackIgnore: true */ process.cwd(),
    "..",
    "data",
    "replica.db",
  );
  return existsSync(replicaPath);
}

export function isServiceHealthApplicable(
  service: string,
  replicaPresent: boolean,
): boolean {
  // REL-055 retired this unscheduled handler: no producer creates the order
  // contract it requires. Historical rows are not evidence of a live control.
  // Keep the row in storage, but do not revive it through the unknown-writer
  // scheduled fallback. Re-enable applicability when a real producer ships.
  if (service === "exit-orders") return false;
  if (service === RETIRED_RESEARCH_SERVICE) return false;
  return service !== REPLICA_WATCHDOG_SERVICE || replicaPresent;
}

type ResearchHealthRow = {
  service?: string;
  state?: string;
  updated_at?: string | null;
};

/**
 * Current stand-in for the retired ingestion review stage.
 * `runs_on` stays empty: marketdesk-research and marketdesk-pdf do not
 * execute that stage. Their rows are the live research health.
 */
export function reviewStageHealth(rows: readonly ResearchHealthRow[]) {
  const live = LIVE_RESEARCH_SERVICES.flatMap((name) => {
    const row = rows.find((item) => item?.service === name);
    if (!row) return [];
    return [{
      service: name,
      state: String(row.state ?? ""),
      updated_at: row.updated_at == null ? null : String(row.updated_at),
    }];
  });
  return {
    retired: true as const,
    retired_service: RETIRED_RESEARCH_SERVICE,
    retired_unit: RETIRED_RESEARCH_UNIT,
    runs_on: [] as string[],
    live_services: live,
  };
}

export function filterApplicableServiceHealthRows<T>(
  rows: readonly T[],
  replicaPresent: boolean = replicaDatabaseExists(),
): T[] {
  return rows.filter((row) => {
    if (typeof row !== "object" || row === null || !("service" in row)) return true;
    return isServiceHealthApplicable(String(row.service ?? ""), replicaPresent);
  });
}

export function filterApplicableServiceHealthBaseline<T>(
  baseline: Readonly<Record<string, T>>,
  replicaPresent: boolean = replicaDatabaseExists(),
): Record<string, T> {
  return Object.fromEntries(
    Object.entries(baseline).filter(([service]) =>
      isServiceHealthApplicable(service, replicaPresent),
    ),
  ) as Record<string, T>;
}
