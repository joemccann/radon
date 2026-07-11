import { existsSync } from "node:fs";
import { resolve } from "node:path";

const REPLICA_WATCHDOG_SERVICE = "replica-watchdog";

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
  return service !== REPLICA_WATCHDOG_SERVICE || replicaPresent;
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
