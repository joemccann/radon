export interface CriCacheData {
  date?: string;
  scan_time?: string;
  history?: Array<Record<string, unknown>>;
  [key: string]: unknown;
}

export interface CriCacheCandidate {
  path: string;
  mtimeMs: number;
  data: CriCacheData;
}

function historyEntries(data: CriCacheData): Array<Record<string, unknown>> {
  return Array.isArray(data.history) ? data.history : [];
}

function numericRealizedVolCount(data: CriCacheData): number {
  return historyEntries(data).filter((entry) => {
    const value = entry.realized_vol;
    return typeof value === "number" && Number.isFinite(value);
  }).length;
}

function candidateTimestamp(candidate: CriCacheCandidate): number {
  const parsed = typeof candidate.data.scan_time === "string"
    ? Date.parse(candidate.data.scan_time)
    : Number.NaN;
  return Number.isFinite(parsed) ? parsed : candidate.mtimeMs;
}

export function hasCompleteRvolHistory(data: CriCacheData): boolean {
  const history = historyEntries(data);
  return history.length >= 20 && numericRealizedVolCount(data) >= 20;
}

export function selectPreferredCriCandidate(
  scheduled: CriCacheCandidate | null,
  legacy: CriCacheCandidate | null,
): CriCacheCandidate | null {
  if (!scheduled) return legacy;
  if (!legacy) return scheduled;

  const scheduledDate = scheduled.data.date ?? "";
  const legacyDate = legacy.data.date ?? "";
  if (scheduledDate !== legacyDate) {
    return scheduledDate > legacyDate ? scheduled : legacy;
  }

  const scheduledComplete = hasCompleteRvolHistory(scheduled.data);
  const legacyComplete = hasCompleteRvolHistory(legacy.data);
  if (scheduledComplete !== legacyComplete) {
    return scheduledComplete ? scheduled : legacy;
  }

  const scheduledRvolCount = numericRealizedVolCount(scheduled.data);
  const legacyRvolCount = numericRealizedVolCount(legacy.data);
  if (scheduledRvolCount !== legacyRvolCount) {
    return scheduledRvolCount > legacyRvolCount ? scheduled : legacy;
  }

  const scheduledHistoryLength = historyEntries(scheduled.data).length;
  const legacyHistoryLength = historyEntries(legacy.data).length;
  if (scheduledHistoryLength !== legacyHistoryLength) {
    return scheduledHistoryLength > legacyHistoryLength ? scheduled : legacy;
  }

  return candidateTimestamp(scheduled) >= candidateTimestamp(legacy) ? scheduled : legacy;
}
