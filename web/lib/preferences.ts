/**
 * Operator preferences client contract.
 *
 * Wire types mirror `ResolvedPreference.to_dict()` in `scripts/app_preferences.py`
 * exactly, and the browser helpers talk only to `/api/preferences` (the Next.js
 * proxy), never to FastAPI directly.
 */

export type PreferenceValueType = "int" | "float" | "bool";
export type PreferenceSource = "db" | "env" | "default";

export type PreferenceEntry = {
  key: string;
  label: string;
  group: string;
  value_type: PreferenceValueType;
  value: number | boolean;
  default: number | boolean;
  hard_min: number | boolean;
  hard_max: number | boolean;
  unit: string;
  description: string;
  applies_immediately: boolean;
  source: PreferenceSource;
  db_rejected: boolean;
  updated_at: string | null;
  updated_by: string | null;
};

export type PreferenceStoreStatus = {
  available: boolean;
  error: string | null;
  checked_at: string | null;
};

export type PreferencesPayload = {
  preferences: PreferenceEntry[];
  groups: string[];
  store: PreferenceStoreStatus;
  generated_at: string;
};

export type PreferenceMutationResult = {
  preference: PreferenceEntry;
  store: PreferenceStoreStatus;
};

export class PreferencesRequestError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = "PreferencesRequestError";
    this.status = status;
    this.code = code;
  }
}

const PREFERENCES_ENDPOINT = "/api/preferences";

function errorFromBody(status: number, body: unknown): PreferencesRequestError {
  const payload = (body ?? {}) as Record<string, unknown>;
  const code = typeof payload.code === "string" ? payload.code : "UPSTREAM_ERROR";
  const message =
    typeof payload.error === "string"
      ? payload.error
      : typeof payload.detail === "string"
        ? payload.detail
        : `HTTP ${status}`;
  return new PreferencesRequestError(status, code, message);
}

async function readJson(res: Response): Promise<unknown> {
  try {
    return await res.json();
  } catch {
    return null;
  }
}

async function requestJson<T>(input: string, init?: RequestInit): Promise<T> {
  const res = await fetch(input, { cache: "no-store", ...init });
  const body = await readJson(res);
  if (!res.ok) {
    throw errorFromBody(res.status, body);
  }
  return body as T;
}

export async function fetchPreferences(): Promise<PreferencesPayload> {
  return requestJson<PreferencesPayload>(PREFERENCES_ENDPOINT, { method: "GET" });
}

export async function savePreference(
  key: string,
  value: number | boolean,
): Promise<PreferenceMutationResult> {
  return requestJson<PreferenceMutationResult>(PREFERENCES_ENDPOINT, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ key, value }),
  });
}

export async function resetPreference(key: string): Promise<PreferenceMutationResult> {
  return requestJson<PreferenceMutationResult>(
    `${PREFERENCES_ENDPOINT}?key=${encodeURIComponent(key)}`,
    { method: "DELETE" },
  );
}

export function groupPreferences(
  entries: PreferenceEntry[],
  groups: string[],
): Array<{ group: string; entries: PreferenceEntry[] }> {
  const buckets = new Map<string, PreferenceEntry[]>();
  for (const entry of entries) {
    const bucket = buckets.get(entry.group);
    if (bucket) {
      bucket.push(entry);
      continue;
    }
    buckets.set(entry.group, [entry]);
  }
  const known = groups.filter((group) => (buckets.get(group)?.length ?? 0) > 0);
  const unknown = [...buckets.keys()]
    .filter((group) => !groups.includes(group))
    .sort((a, b) => a.localeCompare(b));
  return [...known, ...unknown].map((group) => ({
    group,
    entries: buckets.get(group) ?? [],
  }));
}

function formatNumber(entry: PreferenceEntry, value: number): string {
  const maximumFractionDigits = entry.value_type === "int" ? 0 : 2;
  return new Intl.NumberFormat("en-US", {
    minimumFractionDigits: 0,
    maximumFractionDigits,
  }).format(value);
}

export function formatPreferenceValue(entry: PreferenceEntry, value: number | boolean): string {
  const text =
    typeof value === "boolean" ? (value ? "On" : "Off") : formatNumber(entry, value);
  return entry.unit ? `${text} ${entry.unit}` : text;
}

export function parsePreferenceInput(
  entry: PreferenceEntry,
  raw: string,
): { value: number } | { error: string } {
  const trimmed = raw.trim();
  if (!trimmed) return { error: "value is required" };
  const value = Number(trimmed);
  if (!Number.isFinite(value)) return { error: "value must be a number" };
  if (entry.value_type === "int" && !Number.isInteger(value)) {
    return { error: "value must be a whole number" };
  }
  const min = Number(entry.hard_min);
  const max = Number(entry.hard_max);
  if (value < min || value > max) {
    return { error: `value must be between ${entry.hard_min} and ${entry.hard_max}` };
  }
  return { value };
}
