"use client";

/**
 * Operator credentials editor (profile Credentials tab).
 *
 * One card per registry group, one block per vendor service. Values are
 * write-only: configured fields show the server's masked hint, never the
 * value. Submitting runs the vendor check server-side first — a rejected
 * credential saves NOTHING and gets the playful retry line; a vendor outage
 * saves on good faith and says so. MenthorQ / TheMarketEar do a real browser
 * login, so their submit shows a scenic-route delay notice while in flight.
 */

import { useCallback, useEffect, useState } from "react";
import {
  CredentialsRequestError,
  deleteCredential,
  fetchCredentials,
  groupCredentialServices,
  outageNotice,
  playfulRejection,
  saveCredentials,
  slowValidationNotice,
} from "@/lib/credentials";
import type {
  CredentialFieldEntry,
  CredentialServiceEntry,
  CredentialsPayload,
} from "@/lib/credentials";

type ServiceNotice = {
  tone: "ok" | "error" | "info";
  text: string;
};

function errorMessage(error: unknown): string {
  return error instanceof Error && error.message
    ? error.message
    : "credentials request failed";
}

function serviceSlug(id: string): string {
  return id.replace(/_/g, "-");
}

function fieldStatus(field: CredentialFieldEntry): string {
  if (field.configured) return `Stored ${field.hint}`;
  if (field.exported_only) return "Active in this process until restart";
  if (field.env_fallback) return "Using the value from .env";
  return "Not configured";
}

export default function CredentialsPanel() {
  const [payload, setPayload] = useState<CredentialsPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [pageError, setPageError] = useState<string | null>(null);
  // drafts keyed by field name; only non-empty drafts are submitted.
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [busyService, setBusyService] = useState<string | null>(null);
  const [notices, setNotices] = useState<Record<string, ServiceNotice>>({});
  const [attempts, setAttempts] = useState<Record<string, number>>({});

  useEffect(() => {
    let active = true;
    fetchCredentials()
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

  const applyService = useCallback((service: CredentialServiceEntry) => {
    setPayload((current) => {
      if (!current) return current;
      return {
        ...current,
        services: current.services.map((entry) =>
          entry.id === service.id ? service : entry,
        ),
      };
    });
    setDrafts((current) => {
      const next = { ...current };
      for (const field of service.fields) delete next[field.name];
      return next;
    });
  }, []);

  const setNotice = useCallback((serviceId: string, notice: ServiceNotice | null) => {
    setNotices((current) => {
      const next = { ...current };
      if (notice === null) delete next[serviceId];
      else next[serviceId] = notice;
      return next;
    });
  }, []);

  const draftValuesFor = useCallback(
    (service: CredentialServiceEntry): Record<string, string> => {
      const values: Record<string, string> = {};
      for (const field of service.fields) {
        const draft = (drafts[field.name] ?? "").trim();
        if (draft) values[field.name] = draft;
      }
      return values;
    },
    [drafts],
  );

  const submitService = useCallback(
    async (service: CredentialServiceEntry) => {
      const values = draftValuesFor(service);
      if (Object.keys(values).length === 0 || busyService !== null) return;
      setBusyService(service.id);
      setNotice(
        service.id,
        service.slow
          ? { tone: "info", text: slowValidationNotice(service.label) }
          : { tone: "info", text: "Checking with the vendor..." },
      );
      try {
        const result = await saveCredentials(service.id, values);
        applyService(result.service);
        setAttempts((current) => ({ ...current, [service.id]: 0 }));
        if (result.validation.status === "valid") {
          setNotice(service.id, { tone: "ok", text: `${service.label} says the handshake is good. Stored.` });
        } else if (result.validation.status === "error") {
          setNotice(service.id, { tone: "info", text: outageNotice(service.label) });
        } else {
          setNotice(service.id, { tone: "ok", text: "Stored. No live check exists for this one." });
        }
      } catch (error: unknown) {
        if (
          error instanceof CredentialsRequestError &&
          error.code === "CREDENTIAL_REJECTED"
        ) {
          const attempt = attempts[service.id] ?? 0;
          setAttempts((current) => ({ ...current, [service.id]: attempt + 1 }));
          setNotice(service.id, {
            tone: "error",
            text: playfulRejection(service.label, attempt),
          });
        } else {
          setNotice(service.id, { tone: "error", text: errorMessage(error) });
        }
      } finally {
        setBusyService(null);
      }
    },
    [applyService, attempts, busyService, draftValuesFor, setNotice],
  );

  const clearField = useCallback(
    async (service: CredentialServiceEntry, field: CredentialFieldEntry) => {
      if (busyService !== null) return;
      setBusyService(service.id);
      setNotice(service.id, null);
      try {
        const result = await deleteCredential(service.id, field.name);
        applyService(result.service);
      } catch (error: unknown) {
        setNotice(service.id, { tone: "error", text: errorMessage(error) });
      } finally {
        setBusyService(null);
      }
    },
    [applyService, busyService, setNotice],
  );

  if (loading) {
    return (
      <div className="preferences-shell" data-testid="credentials-panel">
        <p data-testid="credentials-loading">Loading credentials</p>
      </div>
    );
  }

  if (pageError || !payload) {
    return (
      <div className="preferences-shell" data-testid="credentials-panel">
        <p className="admin-card-note admin-card-error" role="alert" data-testid="credentials-error">
          {pageError ?? "credentials request failed"}
        </p>
      </div>
    );
  }

  const groups = groupCredentialServices(payload.services, payload.groups);

  return (
    <div className="preferences-shell" data-testid="credentials-panel">
      {groups.map(({ group, services }) => (
        <section className="admin-card preferences-group" key={group}>
          <header className="admin-card-header">
            <span className="admin-card-title">{group}</span>
          </header>

          {services.map((service) => {
            const busy = busyService === service.id;
            const notice = notices[service.id];
            const dirty = Object.keys(draftValuesFor(service)).length > 0;
            const slug = serviceSlug(service.id);
            return (
              <div
                className="preferences-row preferences-row--service"
                data-testid={`credential-service-${slug}`}
                key={service.id}
              >
                <div className="preferences-row__head">
                  <span className="preferences-row__label">{service.label}</span>
                  {service.slow ? (
                    <span className="preferences-badge preferences-badge--restart">
                      SLOW CHECK
                    </span>
                  ) : null}
                  {!service.validator ? (
                    <span className="preferences-badge">NO LIVE CHECK</span>
                  ) : null}
                </div>
                {service.note ? (
                  <p className="preferences-row__description">{service.note}</p>
                ) : null}

                <div className="credentials-fields">
                  {service.fields.map((field) => (
                    <div className="credentials-field" key={field.name}>
                      <label className="credentials-field__meta" htmlFor={`cred-${field.name}`}>
                        <span className="credentials-field__title">
                          <span className="preferences-row__label">{field.label}</span>
                          <code className="preferences-row__key">{field.name}</code>
                        </span>
                        <span className="preferences-row__meta" data-testid={`credential-status-${field.name}`}>
                          {fieldStatus(field)}
                        </span>
                      </label>
                      <div className="credentials-field__inputrow">
                        <input
                          id={`cred-${field.name}`}
                          className="order-input preferences-row__input credentials-field__input"
                          type={field.secret ? "password" : "text"}
                          autoComplete="off"
                          spellCheck={false}
                          placeholder={
                            field.configured
                              ? `Replace ${field.hint}`
                              : field.placeholder || "Paste value"
                          }
                          value={drafts[field.name] ?? ""}
                          disabled={busy}
                          onChange={(e) =>
                            setDrafts((current) => ({
                              ...current,
                              [field.name]: e.target.value,
                            }))
                          }
                        />
                        {field.configured ? (
                          <button
                            type="button"
                            className="admin-btn admin-btn-ghost"
                            data-testid={`credential-clear-${field.name}`}
                            disabled={busy}
                            onClick={() => {
                              void clearField(service, field);
                            }}
                          >
                            Clear
                          </button>
                        ) : null}
                      </div>
                    </div>
                  ))}
                </div>

                <div className="admin-actions-row">
                  <button
                    type="button"
                    className="admin-btn admin-btn-primary"
                    data-testid={`credential-save-${slug}`}
                    disabled={!dirty || busy}
                    onClick={() => {
                      void submitService(service);
                    }}
                  >
                    {busy ? "Checking..." : "Verify and save"}
                  </button>
                </div>

                {notice ? (
                  <p
                    className={
                      notice.tone === "error"
                        ? "admin-card-note admin-card-error"
                        : "admin-card-note"
                    }
                    role={notice.tone === "error" ? "alert" : "status"}
                    data-testid={`credential-notice-${slug}`}
                  >
                    {notice.text}
                  </p>
                ) : null}
              </div>
            );
          })}
        </section>
      ))}
    </div>
  );
}
