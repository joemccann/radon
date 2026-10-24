"use client";
import UwCapabilities from "./UwCapabilities";

import ErrorToast from "@/components/ErrorToast";

/**
 * Operator credentials editor (profile Credentials tab).
 *
 * One card per registry group, one block per vendor service. Values are
 * write-only: configured fields show the server's masked hint, never the
 * value. Submitting runs the vendor check server-side first — a rejected
 * credential saves NOTHING and gets the playful retry line; a vendor outage
 * saves on good faith and says so. MenthorQ / TheMarketEar do a real browser
 * login, so their submit shows a scenic-route delay notice while in flight.
 *
 * IB Gateway is different: saving writes the password on the broker and
 * restarts the Gateway once, which fires an IBKR Mobile 2FA push. Save opens
 * an inline confirm step; Confirm stays disabled until the operator
 * acknowledges the push, and only Confirm fires the PUT.
 */

import { userErrorMessage } from "@/lib/userError";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  CredentialsRequestError,
  GATEWAY_BUSY_NOTICE,
  GATEWAY_CONFIRM_ACK,
  GATEWAY_CONFIRM_COPY,
  GATEWAY_SERVICE_ID,
  GATEWAY_UNKNOWN_NOTICE,
  deleteCredential,
  fetchCredentials,
  gatewayRotationNotice,
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
  return userErrorMessage(error, "Your changes could not be saved. Refresh and try again.");
}

function serviceSlug(id: string): string {
  return id.replace(/_/g, "-");
}

// FastAPI names every rotation outcome with a GATEWAY_ code. Any other 5xx
// (Next UPSTREAM_ERROR, an empty Caddy 502/504, an unhandled 500) and any
// failure with no response at all (timeout, dropped connection) may have
// landed after the broker wrote and restarted, so resubmitting could fire a
// second write, restart and IBKR push. A 4xx the proxy refused before
// FastAPI saved nothing and keeps the generic error.
function gatewayErrorText(error: unknown): string | null {
  if (error instanceof CredentialsRequestError) {
    if (error.code.startsWith("GATEWAY_") || error.code === "CREDENTIAL_STORE_UNAVAILABLE") {
      return error.message;
    }
    return error.status >= 500 ? GATEWAY_UNKNOWN_NOTICE : null;
  }
  return GATEWAY_UNKNOWN_NOTICE;
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
  const [revealedSecrets, setRevealedSecrets] = useState<Record<string, boolean>>({});
  const [busyService, setBusyService] = useState<string | null>(null);
  const [notices, setNotices] = useState<Record<string, ServiceNotice>>({});
  const [attempts, setAttempts] = useState<Record<string, number>>({});
  // IB Gateway confirm step: which service shows it, and whether the operator
  // acknowledged the 2FA push. Both reset after every outcome.
  const [confirmService, setConfirmService] = useState<string | null>(null);
  const [confirmAck, setConfirmAck] = useState(false);
  // A second click lands before the busy state re-renders; this does not.
  const inFlight = useRef(false);

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
        const raw = drafts[field.name] ?? "";
        const draft = raw.trim();
        if (!draft) continue;
        // The Gateway password goes untrimmed: the server rejects edge
        // whitespace instead of this form silently changing the password.
        values[field.name] =
          service.id === GATEWAY_SERVICE_ID && field.name === "TWS_PASSWORD" ? raw : draft;
      }
      return values;
    },
    [drafts],
  );

  const submitService = useCallback(
    async (service: CredentialServiceEntry) => {
      const values = draftValuesFor(service);
      if (Object.keys(values).length === 0 || busyService !== null || inFlight.current) return;
      const gateway = service.id === GATEWAY_SERVICE_ID;
      if (gateway && (confirmService !== service.id || !confirmAck)) return;
      inFlight.current = true;
      setBusyService(service.id);
      setConfirmService(null);
      setConfirmAck(false);
      setNotice(
        service.id,
        gateway
          ? { tone: "info", text: GATEWAY_BUSY_NOTICE }
          : service.slow
            ? { tone: "info", text: slowValidationNotice(service.label) }
            : { tone: "info", text: "Checking with the vendor..." },
      );
      try {
        const result = await saveCredentials(service.id, values);
        applyService(result.service);
        setAttempts((current) => ({ ...current, [service.id]: 0 }));
        if (gateway) {
          setNotice(service.id, gatewayRotationNotice(result.rotation));
        } else if (result.validation.status === "valid") {
          setNotice(service.id, { tone: "ok", text: `${service.label} says the handshake is good. Stored.` });
        } else if (result.validation.status === "error") {
          setNotice(service.id, { tone: "info", text: outageNotice(service.label) });
        } else {
          setNotice(service.id, { tone: "ok", text: "Stored. No live check exists for this one." });
        }
      } catch (error: unknown) {
        const gatewayText = gateway ? gatewayErrorText(error) : null;
        if (gatewayText !== null) {
          setNotice(service.id, { tone: "error", text: gatewayText });
        } else if (
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
        inFlight.current = false;
        setBusyService(null);
        setConfirmService(null);
        setConfirmAck(false);
      }
    },
    [applyService, attempts, busyService, confirmAck, confirmService, draftValuesFor, setNotice],
  );

  const openConfirm = useCallback((serviceId: string) => {
    setConfirmService(serviceId);
    setConfirmAck(false);
  }, []);

  const cancelConfirm = useCallback(() => {
    setConfirmService(null);
    setConfirmAck(false);
  }, []);

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
        <ErrorToast message={pageError ?? "credentials request failed"} testId="credentials-error" />
      </div>
    );
  }

  const groups = groupCredentialServices(payload.services, payload.groups);

  return (
    <div className="preferences-shell" data-testid="credentials-panel">
      {payload.bootstrap_error ? (
        <section className="admin-card preferences-group" role="status" data-testid="credentials-bootstrap-error">
          <header className="admin-card-header">
            <span className="admin-card-title">Encrypted store did not open at boot</span>
            <span className="preferences-badge preferences-badge--rejected">STORED VALUES INACTIVE</span>
          </header>
          <p className="preferences-row__description">
            Every field below is running on its .env fallback, not on what is stored here. Saving still
            writes the store; the running process picks stored values up at the next FastAPI restart.
            Detail: {payload.bootstrap_error}
          </p>
        </section>
      ) : null}
      {groups.map(({ group, services }) => (
        <section className="admin-card preferences-group" key={group}>
          <header className="admin-card-header preferences-group__header">
            <h2 className="admin-card-title">{group}</h2>
          </header>

          {services.map((service) => {
            const busy = busyService === service.id;
            const notice = notices[service.id];
            const dirty = Object.keys(draftValuesFor(service)).length > 0;
            const slug = serviceSlug(service.id);
            const gateway = service.id === GATEWAY_SERVICE_ID;
            const confirming = gateway && confirmService === service.id && !busy;
            const configuredCount = service.fields.filter((field) =>
              field.configured || field.env_fallback || field.exported_only,
            ).length;
            return (
              <div
                className="preferences-row preferences-row--service settings-service"
                data-testid={`credential-service-${slug}`}
                key={service.id}
              >
                <div className="preferences-row__head">
                  <h3 className="preferences-row__label">{service.label}</h3>
                  {service.slow ? (
                    <span className="preferences-badge preferences-badge--restart">
                      Longer verification
                    </span>
                  ) : null}
                  {!service.validator ? (
                    <span className="preferences-badge">Not checked live</span>
                  ) : null}
                  <span className="settings-service__status">
                    {configuredCount} of {service.fields.length} configured
                  </span>
                </div>
                {service.note ? (
                  <p className="preferences-row__description">{service.note}</p>
                ) : null}

                {service.id === "unusual_whales" ? <UwCapabilities /> : null}
                <div className="credentials-fields">
                  {service.fields.map((field) => (
                    <div className="credentials-field" key={field.name}>
                      <div className="credentials-field__meta">
                        <span className="credentials-field__title">
                          <label className="preferences-row__label" htmlFor={`cred-${field.name}`}>
                            {field.label}
                          </label>
                        </span>
                        <span
                          id={`credential-status-${field.name}`}
                          className="preferences-row__meta"
                          data-testid={`credential-status-${field.name}`}
                        >
                          {fieldStatus(field)}
                        </span>
                        <details className="credentials-field__details">
                          <summary>Field name</summary>
                          <code className="preferences-row__key">{field.name}</code>
                        </details>
                      </div>
                      <div className="credentials-field__inputrow">
                        <input
                          id={`cred-${field.name}`}
                          className="order-input preferences-row__input credentials-field__input"
                          type={field.secret && !revealedSecrets[field.name] ? "password" : "text"}
                          autoComplete="off"
                          spellCheck={false}
                          autoCapitalize="none"
                          autoCorrect="off"
                          aria-describedby={`credential-status-${field.name}`}
                          placeholder={
                            field.configured
                              ? "Enter replacement"
                              : field.placeholder || `Paste ${field.label.toLowerCase()}`
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
                        {field.secret ? (
                          <button
                            type="button"
                            className="admin-btn admin-btn-ghost credentials-field__reveal"
                            aria-label={`${revealedSecrets[field.name] ? "Hide" : "Show"} ${field.label}`}
                            aria-pressed={Boolean(revealedSecrets[field.name])}
                            disabled={busy}
                            onClick={() => setRevealedSecrets((current) => ({
                              ...current,
                              [field.name]: !current[field.name],
                            }))}
                          >
                            {revealedSecrets[field.name] ? "Hide" : "Show"}
                          </button>
                        ) : null}
                        {field.configured ? (
                          <button
                            type="button"
                            className="admin-btn admin-btn-ghost credentials-field__clear"
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
                  <span className="settings-draft-status" role="status">
                    {dirty ? "Unsaved changes" : "Stored values stay hidden"}
                  </span>
                  <button
                    type="button"
                    className="admin-btn admin-btn-primary"
                    data-testid={`credential-save-${slug}`}
                    disabled={!dirty || busy || confirming}
                    onClick={() => {
                      if (gateway) openConfirm(service.id);
                      else void submitService(service);
                    }}
                  >
                    {gateway
                      ? busy ? "Restarting Gateway..." : "Save and restart Gateway"
                      : busy ? "Checking..." : "Verify and save"}
                  </button>
                </div>

                {confirming ? (
                  <div
                    className="credentials-confirm"
                    role="group"
                    aria-labelledby={`credential-confirm-title-${slug}`}
                    data-testid={`credential-confirm-${slug}`}
                  >
                    <p className="admin-confirm-title" id={`credential-confirm-title-${slug}`}>
                      Restart IB Gateway with the new password?
                    </p>
                    <p className="admin-confirm-body">{GATEWAY_CONFIRM_COPY}</p>
                    <label className="credentials-confirm__ack">
                      <input
                        type="checkbox"
                        data-testid={`credential-confirm-ack-${slug}`}
                        checked={confirmAck}
                        onChange={(e) => setConfirmAck(e.target.checked)}
                      />
                      <span>{GATEWAY_CONFIRM_ACK}</span>
                    </label>
                    <div className="admin-confirm-actions">
                      <button
                        type="button"
                        className="admin-btn admin-btn-ghost"
                        data-testid={`credential-confirm-cancel-${slug}`}
                        onClick={cancelConfirm}
                      >
                        Cancel
                      </button>
                      <button
                        type="button"
                        className="admin-btn admin-btn-primary"
                        data-testid={`credential-confirm-submit-${slug}`}
                        disabled={!confirmAck || !dirty}
                        onClick={() => {
                          void submitService(service);
                        }}
                      >
                        Confirm and restart
                      </button>
                    </div>
                  </div>
                ) : null}

                {notice?.tone === "error" ? <ErrorToast message={notice.text} testId={`credential-notice-${slug}`} /> : notice ? (
                  <p
                    className="admin-card-note"
                    role="status"
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
