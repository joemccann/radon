/** Turso row written by the disabled radon-research.service. */
export const RETIRED_RESEARCH_SERVICE = "dropbox-research";
export const RETIRED_RESEARCH_UNIT = "radon-research.service";
/**
 * Live research health. Neither service runs the ingestion review stage.
 * marketdesk-research publishes articles. marketdesk-pdf archives PDFs.
 */
export const LIVE_RESEARCH_SERVICES = ["marketdesk-research", "marketdesk-pdf"] as const;

/**
 * Writers that no longer exist. Their latest-state rows and transition
 * history stay in Turso but are not evidence of a live control, so they
 * must not surface through the unknown-writer scheduled fallback.
 * - exit-orders: handler deleted; Radon places no automated exits.
 * - dropbox-research: radon-research.service is disabled.
 * - replica-watchdog: handler deleted with the retired embedded replica.
 */
const RETIRED_SERVICES: ReadonlySet<string> = new Set([
  "exit-orders",
  RETIRED_RESEARCH_SERVICE,
  "replica-watchdog",
]);

export function isServiceHealthApplicable(service: string): boolean {
  return !RETIRED_SERVICES.has(service);
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

export function filterApplicableServiceHealthRows<T>(rows: readonly T[]): T[] {
  return rows.filter((row) => {
    if (typeof row !== "object" || row === null || !("service" in row)) return true;
    return isServiceHealthApplicable(String(row.service ?? ""));
  });
}

export function filterApplicableServiceHealthBaseline<T>(
  baseline: Readonly<Record<string, T>>,
): Record<string, T> {
  return Object.fromEntries(
    Object.entries(baseline).filter(([service]) => isServiceHealthApplicable(service)),
  ) as Record<string, T>;
}
