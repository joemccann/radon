"use client";

/**
 * Server-synced UI preferences (theme, table column visibility).
 *
 * Promoted from localStorage-only on 2026-09-01: localStorage stays the
 * pre-paint cache on each device (ThemeBootstrap reads it synchronously),
 * this module owns the cross-device copy on the user_profiles row.
 *
 * Read path: one shared GET /api/profile per page load, single-flight.
 * Write path: merged into a module cache immediately, flushed as ONE
 * debounced PUT /api/profile carrying the whole ui_preferences object.
 * Writes are fire-and-forget: a failed sync never breaks the local UI,
 * the preference simply stays device-local until the next save.
 */

export type UiTheme = "dark" | "light";

export type UiPreferences = {
  theme?: UiTheme;
  columns?: Record<string, Record<string, boolean>>;
};

const FLUSH_DELAY_MS = 800;

let cache: UiPreferences | null = null;
let hydratePromise: Promise<UiPreferences> | null = null;
let flushTimer: ReturnType<typeof setTimeout> | null = null;

function sanitize(raw: unknown): UiPreferences {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return {};
  const source = raw as Record<string, unknown>;
  const prefs: UiPreferences = {};
  if (source.theme === "dark" || source.theme === "light") {
    prefs.theme = source.theme;
  }
  if (source.columns && typeof source.columns === "object" && !Array.isArray(source.columns)) {
    const columns: Record<string, Record<string, boolean>> = {};
    for (const [tableId, table] of Object.entries(source.columns as Record<string, unknown>)) {
      if (!table || typeof table !== "object" || Array.isArray(table)) continue;
      const entry: Record<string, boolean> = {};
      for (const [key, value] of Object.entries(table as Record<string, unknown>)) {
        if (typeof value === "boolean") entry[key] = value;
      }
      columns[tableId] = entry;
    }
    prefs.columns = columns;
  }
  return prefs;
}

export function mergeUiPreferences(
  local: UiPreferences | null,
  server: UiPreferences,
): UiPreferences {
  if (!local) return server;
  const merged: UiPreferences = { ...local };
  if (local.theme === undefined && server.theme !== undefined) {
    merged.theme = server.theme;
  }
  if (server.columns) {
    const columns = { ...(local.columns ?? {}) };
    for (const [tableId, table] of Object.entries(server.columns)) {
      // REL-222 (R-594): LOCAL wins per key, matching the theme half — the
      // server copy fills gaps only. Server-wins reverted a column toggled
      // before hydrate resolved (the clobber class the theme fix closed).
      columns[tableId] = { ...table, ...(columns[tableId] ?? {}) };
    }
    merged.columns = columns;
  }
  return merged;
}

export function hydrateUiPreferences(): Promise<UiPreferences> {
  if (cache) return Promise.resolve(cache);
  if (!hydratePromise) {
    hydratePromise = fetch("/api/profile", { cache: "no-store" })
      .then(async (res) => {
        if (!res.ok) throw new Error(`profile hydrate ${res.status}`);
        const json = (await res.json()) as { ui_preferences?: unknown };
        return sanitize(json.ui_preferences);
      })
      .then((prefs) => {
        cache = mergeUiPreferences(cache, prefs);
        return cache ?? {};
      })
      .catch(() => {
        hydratePromise = null;
        return cache ?? {};
      });
  }
  return hydratePromise;
}

function scheduleFlush(): void {
  if (flushTimer) clearTimeout(flushTimer);
  flushTimer = setTimeout(() => {
    flushTimer = null;
    void flushUiPreferences();
  }, FLUSH_DELAY_MS);
}

async function flushUiPreferences(): Promise<void> {
  if (!cache) return;
  try {
    await fetch("/api/profile", {
      method: "PUT",
      cache: "no-store",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ui_preferences: cache }),
    });
  } catch {
    // fire-and-forget: the preference stays device-local until the next save
  }
}

export function saveUiTheme(theme: UiTheme): void {
  cache = { ...(cache ?? {}), theme };
  scheduleFlush();
}

export function saveUiColumns(tableId: string, visible: Record<string, boolean>): void {
  const current = cache ?? {};
  cache = {
    ...current,
    columns: { ...(current.columns ?? {}), [tableId]: { ...visible } },
  };
  scheduleFlush();
}

/** Test-only: reset module state between cases. */
export function __resetUiPreferencesForTests(): void {
  cache = null;
  hydratePromise = null;
  if (flushTimer) clearTimeout(flushTimer);
  flushTimer = null;
}

/** Test-only: seed the in-memory cache without a network round-trip. */
export function __seedUiPreferencesForTests(prefs: UiPreferences): void {
  cache = prefs;
}
