"use client";

/**
 * Operator preferences surface for /preferences.
 *
 * Renders one card per registry group and one row per preference, each showing
 * the effective value, where it came from (db / env / default), the code
 * default and the allowed band. Nothing is optimistic: a row only ever renders
 * the server's value, so a rejected save leaves the displayed value untouched.
 */

import { useCallback, useEffect, useState } from "react";
import ConfirmDialog from "@/components/admin/ConfirmDialog";
import {
  fetchPreferences,
  formatPreferenceValue,
  groupPreferences,
  parsePreferenceInput,
  resetPreference,
  savePreference,
} from "@/lib/preferences";
import type { PreferenceEntry, PreferenceMutationResult, PreferencesPayload } from "@/lib/preferences";

/** The group whose values bound how much money one order can move. */
const RISK_GROUP = "Order Limits";

function groupSlug(group: string): string {
  return group.toLowerCase().replace(/\s+/g, "-");
}

/**
 * Loosening a fat-finger stop is the only edit here that can cost money, and an
 * extra typed zero is exactly the mistake these caps exist to catch. Raising
 * one takes the same type-to-confirm gate as the gateway Stop control.
 * Tightening a cap, or changing anything outside Order Limits, saves directly.
 */
function isWidening(entry: PreferenceEntry, next: number | boolean): boolean {
  if (entry.group !== RISK_GROUP) return false;
  if (typeof next !== "number" || typeof entry.value !== "number") return false;
  return next > entry.value;
}

function errorMessage(error: unknown): string {
  return error instanceof Error && error.message ? error.message : "preferences request failed";
}

function rangeLabel(entry: PreferenceEntry): string {
  if (entry.value_type === "bool") {
    return "Allowed On or Off";
  }
  return `Allowed ${entry.hard_min} to ${entry.hard_max}`;
}

export default function PreferencesSection() {
  const [payload, setPayload] = useState<PreferencesPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [pageError, setPageError] = useState<string | null>(null);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [boolDrafts, setBoolDrafts] = useState<Record<string, boolean>>({});
  const [rowBusy, setRowBusy] = useState<Record<string, boolean>>({});
  const [rowError, setRowError] = useState<Record<string, string>>({});
  const [pendingWiden, setPendingWiden] = useState<
    { entry: PreferenceEntry; value: number | boolean } | null
  >(null);

  useEffect(() => {
    let active = true;
    fetchPreferences()
      .then((next) => {
        if (!active) return;
        setPayload(next);
        setPageError(null);
      })
      .catch((error: unknown) => {
        if (!active) return;
        setPageError(errorMessage(error));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  const applyMutation = useCallback((key: string, result: PreferenceMutationResult) => {
    setPayload((current) => {
      if (!current) return current;
      return {
        ...current,
        preferences: current.preferences.map((entry) => (entry.key === key ? result.preference : entry)),
        store: result.store,
      };
    });
    setDrafts((current) => {
      const next = { ...current };
      delete next[key];
      return next;
    });
    setBoolDrafts((current) => {
      const next = { ...current };
      delete next[key];
      return next;
    });
    setRowError((current) => {
      const next = { ...current };
      delete next[key];
      return next;
    });
  }, []);

  const setBusy = useCallback((key: string, busy: boolean) => {
    setRowBusy((current) => ({ ...current, [key]: busy }));
  }, []);

  const draftFor = (entry: PreferenceEntry): string => drafts[entry.key] ?? String(entry.value);
  const boolDraftFor = (entry: PreferenceEntry): boolean => boolDrafts[entry.key] ?? Boolean(entry.value);

  const inputErrorFor = (entry: PreferenceEntry): string | null => {
    if (entry.value_type === "bool") return null;
    const draft = drafts[entry.key];
    if (draft === undefined) return null;
    const parsed = parsePreferenceInput(entry, draft);
    return "error" in parsed ? parsed.error : null;
  };

  const isDirty = (entry: PreferenceEntry): boolean => (
    entry.value_type === "bool"
      ? boolDraftFor(entry) !== entry.value
      : draftFor(entry) !== String(entry.value)
  );

  const draftValueFor = useCallback((entry: PreferenceEntry): number | boolean | null => {
    if (entry.value_type === "bool") {
      return boolDrafts[entry.key] ?? Boolean(entry.value);
    }
    const parsed = parsePreferenceInput(entry, drafts[entry.key] ?? String(entry.value));
    if ("error" in parsed) {
      setRowError((current) => ({ ...current, [entry.key]: parsed.error }));
      return null;
    }
    return parsed.value;
  }, [boolDrafts, drafts]);

  const commitSave = useCallback(async (
    entry: PreferenceEntry,
    value: number | boolean,
  ): Promise<boolean> => {
    setBusy(entry.key, true);
    setRowError((current) => {
      const next = { ...current };
      delete next[entry.key];
      return next;
    });
    try {
      const result = await savePreference(entry.key, value);
      applyMutation(entry.key, result);
      return true;
    } catch (error: unknown) {
      setRowError((current) => ({ ...current, [entry.key]: errorMessage(error) }));
      return false;
    } finally {
      setBusy(entry.key, false);
    }
  }, [applyMutation, setBusy]);

  const saveRow = useCallback(async (entry: PreferenceEntry): Promise<boolean> => {
    const value = draftValueFor(entry);
    if (value === null) return false;
    if (isWidening(entry, value)) {
      setPendingWiden({ entry, value });
      return false;
    }
    return commitSave(entry, value);
  }, [commitSave, draftValueFor]);

  const resetRow = useCallback(async (entry: PreferenceEntry) => {
    setBusy(entry.key, true);
    setRowError((current) => {
      const next = { ...current };
      delete next[entry.key];
      return next;
    });
    try {
      const result = await resetPreference(entry.key);
      applyMutation(entry.key, result);
    } catch (error: unknown) {
      setRowError((current) => ({ ...current, [entry.key]: errorMessage(error) }));
    } finally {
      setBusy(entry.key, false);
    }
  }, [applyMutation, setBusy]);

  const saveGroup = useCallback(async (entries: PreferenceEntry[]) => {
    for (const entry of entries.filter(isDirty)) {
      const ok = await saveRow(entry);
      if (!ok) return;
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [saveRow, drafts, boolDrafts, payload]);

  if (loading) {
    return (
      <div className="preferences-shell" data-testid="preferences-section">
        <p data-testid="preferences-loading">Loading preferences</p>
      </div>
    );
  }

  if (pageError || !payload) {
    return (
      <div className="preferences-shell" data-testid="preferences-section">
        <p className="admin-card-note admin-card-error" role="alert" data-testid="preferences-error">
          {pageError ?? "preferences request failed"}
        </p>
      </div>
    );
  }

  const storeUnavailable = payload.store.available === false;
  const groups = groupPreferences(payload.preferences, payload.groups);

  return (
    <div className="preferences-shell" data-testid="preferences-section">
      {storeUnavailable ? (
        <p className="admin-card-note preferences-store-banner" role="status" data-testid="preferences-store-banner">
          Preferences store unavailable. Showing environment and code defaults. Saving is disabled.
        </p>
      ) : null}

      {groups.map(({ group, entries }) => {
        const slug = groupSlug(group);
        const groupDirty = entries.some(isDirty);
        return (
          <section className="admin-card preferences-group" data-testid={`preferences-group-${slug}`} key={group}>
            <header className="admin-card-header">
              <span className="admin-card-title">{group}</span>
              <div className="admin-actions-row">
                <button
                  type="button"
                  className="admin-btn admin-btn-primary"
                  data-testid={`preference-group-save-${slug}`}
                  disabled={!groupDirty || storeUnavailable}
                  onClick={() => { void saveGroup(entries); }}
                >
                  Save group
                </button>
              </div>
            </header>

            {entries.map((entry) => {
              const busy = Boolean(rowBusy[entry.key]);
              const inputError = inputErrorFor(entry);
              const message = rowError[entry.key] ?? inputError;
              const dirty = isDirty(entry);
              return (
                <div className="preferences-row" data-testid={`preference-row-${entry.key}`} key={entry.key}>
                  <div className="preferences-row__head">
                    <span className="preferences-row__label">{entry.label}</span>
                    <code className="preferences-row__key">{entry.key}</code>
                  </div>

                  <p className="preferences-row__description">{entry.description}</p>

                  <div className="preferences-row__metaline">
                    <span
                      className={`preferences-badge preferences-badge--${entry.source}`}
                      data-testid={`preference-source-${entry.key}`}
                    >
                      {entry.source.toUpperCase()}
                    </span>
                    <span className="preferences-row__meta" data-testid={`preference-default-${entry.key}`}>
                      Default {formatPreferenceValue(entry, entry.default)}
                    </span>
                    <span className="preferences-row__meta" data-testid={`preference-range-${entry.key}`}>
                      {rangeLabel(entry)}
                    </span>
                    {entry.applies_immediately ? null : (
                      <span
                        className="preferences-badge preferences-badge--restart"
                        data-testid={`preference-restart-badge-${entry.key}`}
                        title="Stored now. The consuming process reads this when it next starts."
                      >
                        RESTART REQUIRED
                      </span>
                    )}
                    {entry.db_rejected ? (
                      <>
                        <span
                          className="preferences-badge preferences-badge--rejected"
                          data-testid={`preference-rejected-${entry.key}`}
                        >
                          STORED VALUE IGNORED
                        </span>
                        <span className="preferences-row__meta">
                          A stored value was outside the allowed range and was ignored.
                        </span>
                      </>
                    ) : null}
                  </div>

                  <div className="preferences-row__control">
                    {entry.value_type === "bool" ? (
                      <label className="preferences-row__toggle">
                        <input
                          type="checkbox"
                          checked={boolDraftFor(entry)}
                          onChange={(e) => setBoolDrafts((current) => ({ ...current, [entry.key]: e.target.checked }))}
                          data-testid={`preference-input-${entry.key}`}
                          aria-label={entry.label}
                        />
                        <span>{boolDraftFor(entry) ? "On" : "Off"}</span>
                      </label>
                    ) : (
                      <input
                        type="number"
                        step={entry.value_type === "int" ? "1" : "any"}
                        min={Number(entry.hard_min)}
                        max={Number(entry.hard_max)}
                        inputMode={entry.value_type === "int" ? "numeric" : "decimal"}
                        className="order-input preferences-row__input"
                        value={draftFor(entry)}
                        onChange={(e) => {
                          const raw = e.target.value;
                          setDrafts((current) => ({ ...current, [entry.key]: raw }));
                          setRowError((current) => {
                            const next = { ...current };
                            delete next[entry.key];
                            return next;
                          });
                        }}
                        data-testid={`preference-input-${entry.key}`}
                        aria-label={entry.label}
                      />
                    )}
                  </div>

                  <div className="admin-actions-row">
                    <button
                      type="button"
                      className="admin-btn admin-btn-primary"
                      data-testid={`preference-save-${entry.key}`}
                      disabled={!dirty || busy || storeUnavailable || Boolean(inputError)}
                      onClick={() => { void saveRow(entry); }}
                    >
                      Save
                    </button>
                    <button
                      type="button"
                      className="admin-btn admin-btn-ghost"
                      data-testid={`preference-reset-${entry.key}`}
                      disabled={busy || storeUnavailable || entry.source !== "db"}
                      title={entry.source !== "db" ? "No stored override to reset" : undefined}
                      onClick={() => { void resetRow(entry); }}
                    >
                      Reset to default
                    </button>
                  </div>

                  {message ? (
                    <p
                      className="admin-card-note admin-card-error"
                      role="alert"
                      data-testid={`preference-error-${entry.key}`}
                    >
                      {message}
                    </p>
                  ) : null}
                </div>
              );
            })}
          </section>
        );
      })}

      {pendingWiden ? (
        <div data-testid="preference-widen-confirm">
          <ConfirmDialog
            open
            destructive
            title="Widen a risk cap"
            body={
              `${pendingWiden.entry.label} (${pendingWiden.entry.key}) goes from ` +
              `${formatPreferenceValue(pendingWiden.entry, pendingWiden.entry.value)} to ` +
              `${formatPreferenceValue(pendingWiden.entry, pendingWiden.value)}. ` +
              "This loosens a fat finger stop on live order placement and is recorded in the audit log."
            }
            confirmLabel="Widen cap"
            requireTyped={pendingWiden.entry.key}
            onConfirm={() => {
              const { entry, value } = pendingWiden;
              setPendingWiden(null);
              void commitSave(entry, value);
            }}
            onCancel={() => setPendingWiden(null)}
          />
        </div>
      ) : null}
    </div>
  );
}
