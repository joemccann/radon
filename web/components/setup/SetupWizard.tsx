"use client";

import ErrorToast from "@/components/ErrorToast";

/**
 * First-run setup wizard (setup mode only — no Clerk keys configured yet).
 *
 * Step 1 proves terminal possession with the console token. Step 2 collects
 * bootstrap credentials (Clerk, Turso) and any vendor keys, with per-service
 * async vendor checks and the playful retry copy. Step 3 stores everything
 * (encrypted secret store + .env materialization) and hands over restart
 * instructions. Bring-your-own accounts: each service links nowhere fancy,
 * the operator pastes keys from the vendor dashboards they already have.
 */

import { userErrorMessage } from "@/lib/userError";
import { useCallback, useMemo, useRef, useState } from "react";
import type { CredentialServiceEntry, CredentialsPayload } from "@/lib/credentials";
import {
  groupCredentialServices,
  playfulRejection,
  slowValidationNotice,
} from "@/lib/credentials";

const BOOTSTRAP_SERVICE_IDS = new Set(["clerk", "turso"]);

type Step = "token" | "collect" | "done";

type StatusResponse = {
  ok: boolean;
  backend: boolean;
  credentials: CredentialsPayload | null;
};

type Verdict = { status: string; message: string };

/**
 * The collect step does one thing at a time: idle, one vendor check, or the
 * final store. A check and the store can never overlap, so the store never
 * runs on values whose verdict is still being decided.
 */
type Activity =
  | { kind: "idle" }
  | { kind: "validating"; service: string }
  | { kind: "completing" };

const IDLE: Activity = { kind: "idle" };

type CompleteResponse = {
  ok: boolean;
  backend: boolean;
  outcomes: Array<{ service: string; stored: boolean; validation: Verdict }>;
  written: string[];
  restart_required: boolean;
};

async function postJson<T>(url: string, body: unknown): Promise<T> {
  const res = await fetch(url, {
    method: "POST",
    cache: "no-store",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(220_000),
  });
  const json = (await res.json().catch(() => null)) as
    | (T & { error?: string })
    | null;
  if (!res.ok) {
    throw new Error(
      (json as { error?: string } | null)?.error ?? `HTTP ${res.status}`,
    );
  }
  return json as T;
}

export default function SetupWizard() {
  const [step, setStep] = useState<Step>("token");
  const [token, setToken] = useState("");
  const [tokenError, setTokenError] = useState<string | null>(null);
  const [tokenBusy, setTokenBusy] = useState(false);
  const [backendUp, setBackendUp] = useState(true);
  const [registry, setRegistry] = useState<CredentialsPayload | null>(null);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [verdicts, setVerdicts] = useState<Record<string, Verdict>>({});
  const [attempts, setAttempts] = useState<Record<string, number>>({});
  const [activity, setActivityState] = useState<Activity>(IDLE);
  // Synchronous mirror so two clicks in one tick cannot both leave idle.
  const activityRef = useRef<Activity>(IDLE);
  const setActivity = useCallback((next: Activity) => {
    activityRef.current = next;
    setActivityState(next);
  }, []);
  const busyService = activity.kind === "validating" ? activity.service : null;
  const completing = activity.kind === "completing";
  const [completeError, setCompleteError] = useState<string | null>(null);
  const [result, setResult] = useState<CompleteResponse | null>(null);

  const submitToken = useCallback(async () => {
    setTokenBusy(true);
    setTokenError(null);
    try {
      const status = await postJson<StatusResponse>("/api/setup/status", { token });
      setBackendUp(status.backend);
      setRegistry(status.credentials);
      setStep("collect");
    } catch (error) {
      setTokenError(userErrorMessage(error, "token check failed"));
    } finally {
      setTokenBusy(false);
    }
  }, [token]);

  const valuesFor = useCallback(
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

  const validateService = useCallback(
    async (service: CredentialServiceEntry) => {
      const values = valuesFor(service);
      if (Object.keys(values).length === 0 || activityRef.current.kind !== "idle") return;
      setActivity({ kind: "validating", service: service.id });
      setVerdicts((current) => ({
        ...current,
        [service.id]: {
          status: "checking",
          message: service.slow
            ? slowValidationNotice(service.label)
            : "Checking with the vendor...",
        },
      }));
      try {
        const data = await postJson<{ validation: Verdict }>("/api/setup/validate", {
          token,
          service: service.id,
          values,
        });
        let verdict = data.validation;
        if (verdict.status === "invalid") {
          const attempt = attempts[service.id] ?? 0;
          setAttempts((current) => ({ ...current, [service.id]: attempt + 1 }));
          verdict = { status: "invalid", message: playfulRejection(service.label, attempt) };
        }
        setVerdicts((current) => ({ ...current, [service.id]: verdict }));
      } catch (error) {
        setVerdicts((current) => ({
          ...current,
          [service.id]: {
            status: "error",
            message: userErrorMessage(error, "check failed"),
          },
        }));
      } finally {
        setActivity(IDLE);
      }
    },
    [attempts, setActivity, token, valuesFor],
  );

  const complete = useCallback(async () => {
    if (!registry || activityRef.current.kind !== "idle") return;
    const services: Record<string, Record<string, string>> = {};
    for (const service of registry.services) {
      const values = valuesFor(service);
      if (Object.keys(values).length > 0) services[service.id] = values;
    }
    if (Object.keys(services).length === 0) {
      setCompleteError("Nothing to save yet. Paste at least the Clerk and Turso credentials.");
      return;
    }
    setActivity({ kind: "completing" });
    setCompleteError(null);
    try {
      const data = await postJson<CompleteResponse>("/api/setup/complete", {
        token,
        services,
      });
      setResult(data);
      setStep("done");
    } catch (error) {
      setCompleteError(userErrorMessage(error, "setup failed"));
    } finally {
      setActivity(IDLE);
    }
  }, [registry, setActivity, token, valuesFor]);

  // A verdict describes the values it checked; an edit retires it.
  const editDraft = useCallback((serviceId: string, field: string, value: string) => {
    setDrafts((current) => ({ ...current, [field]: value }));
    setVerdicts((current) => {
      if (!(serviceId in current)) return current;
      const next = { ...current };
      delete next[serviceId];
      return next;
    });
  }, []);

  const grouped = useMemo(() => {
    if (!registry) return [];
    const bootstrapFirst = [...registry.services].sort((a, b) => {
      const aBoot = BOOTSTRAP_SERVICE_IDS.has(a.id) ? 0 : 1;
      const bBoot = BOOTSTRAP_SERVICE_IDS.has(b.id) ? 0 : 1;
      return aBoot - bBoot;
    });
    return groupCredentialServices(bootstrapFirst, [
      "Infrastructure",
      ...registry.groups.filter((group) => group !== "Infrastructure"),
    ]);
  }, [registry]);

  if (step === "token") {
    return (
      <main className="preferences-shell" data-testid="setup-wizard" style={{ maxWidth: 560, margin: "48px auto", padding: "0 16px" }}>
        <section className="admin-card preferences-group">
          <header className="admin-card-header">
            <h1 className="admin-card-title">Paste the setup token from your terminal</h1>
          </header>
          <p className="preferences-row__description">
            The terminal that launched Radon printed it. No credentials are
            configured yet, so every route lands here until setup finishes.
          </p>
          <div className="preferences-row__control">
            <input
              className="order-input preferences-row__input setup-token-input"
              type="password"
              autoComplete="off"
              aria-label="Setup token"
              placeholder="Setup token"
              value={token}
              data-testid="setup-token-input"
              onChange={(e) => setToken(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") void submitToken();
              }}
            />
            <button
              type="button"
              className="admin-btn admin-btn-primary setup-token-submit"
              data-testid="setup-token-submit"
              disabled={!token.trim() || tokenBusy}
              onClick={() => {
                void submitToken();
              }}
            >
              {tokenBusy ? "Checking..." : "Begin setup"}
            </button>
          </div>
          {tokenError ? (
            <ErrorToast message={tokenError} testId="setup-token-error" />
          ) : null}
        </section>
      </main>
    );
  }

  if (step === "done" && result) {
    return (
      <main className="preferences-shell" data-testid="setup-wizard" style={{ maxWidth: 640, margin: "48px auto", padding: "0 16px" }}>
        <section className="admin-card preferences-group">
          <header className="admin-card-header">
            <span className="admin-card-title">Setup stored</span>
          </header>
          <ul className="preferences-row__description" data-testid="setup-outcomes">
            {result.outcomes.map((outcome) => (
              <li key={outcome.service}>
                {outcome.service}: {outcome.stored ? "stored" : "NOT stored"}
                {outcome.validation.status === "invalid" || outcome.validation.status === "error" ? (
                  <ErrorToast message={`${outcome.service}: ${userErrorMessage(outcome.validation.message, "Credential validation did not complete. Try again.")}`} />
                ) : outcome.validation.message ? ` (${userErrorMessage(outcome.validation.message, "Credential validation did not complete. Try again.")})` : ""}
              </li>
            ))}
          </ul>
          <p className="preferences-row__description">
            Env files written: {result.written.join(", ") || "none"}.
          </p>
          <p className="preferences-row__description" data-testid="setup-restart-note">
            Last step is yours: restart the stack (npm run dev, or your service
            manager) so Next.js boots with the Clerk keys. Sign up with your
            own account, then put its user id in ALLOWED_USER_IDS to claim
            operator access. This wizard retires itself the moment auth is up.
          </p>
        </section>
      </main>
    );
  }

  return (
    <main className="preferences-shell" data-testid="setup-wizard" style={{ maxWidth: 720, margin: "48px auto", padding: "0 16px" }}>
      {!backendUp ? (
        <ErrorToast message="Live credential checks are unavailable. You can still save values and finish setup." />
      ) : null}
      {!registry ? (
        <section className="admin-card preferences-group">
          <ErrorToast message="The credential registry is unavailable. Start the stack and reload, or finish later from the profile page." />
          <button type="button" className="btn-secondary" onClick={() => window.location.reload()}>Reload setup</button>
        </section>
      ) : (
        grouped.map(({ group, services }) => (
          <section className="admin-card preferences-group" key={group}>
            <header className="admin-card-header">
              <span className="admin-card-title">{group}</span>
            </header>
            {services.map((service) => {
              const busy = busyService === service.id;
              const verdict = verdicts[service.id];
              const hasValues = Object.keys(valuesFor(service)).length > 0;
              return (
                <div className="preferences-row preferences-row--service" data-testid={`setup-service-${service.id}`} key={service.id}>
                  <div className="preferences-row__head">
                    <span className="preferences-row__label">
                      {service.label}
                      {BOOTSTRAP_SERVICE_IDS.has(service.id) ? " (required)" : ""}
                    </span>
                    {service.slow ? (
                      <span className="preferences-badge preferences-badge--restart">SLOW CHECK</span>
                    ) : null}
                  </div>
                  {service.note ? (
                    <p className="preferences-row__description">{service.note}</p>
                  ) : null}
                  <div className="credentials-fields">
                    {service.fields.map((field) => (
                      <div className="credentials-field" key={field.name}>
                        <label className="credentials-field__meta" htmlFor={`setup-${field.name}`}>
                          <span className="credentials-field__title">
                            <span className="preferences-row__label">{field.label}</span>
                            <code className="preferences-row__key">{field.name}</code>
                          </span>
                        </label>
                        <div className="credentials-field__inputrow">
                          <input
                            id={`setup-${field.name}`}
                            className="order-input preferences-row__input credentials-field__input"
                            type={field.secret ? "password" : "text"}
                            autoComplete="off"
                            spellCheck={false}
                            placeholder={field.placeholder || "Paste value"}
                            value={drafts[field.name] ?? ""}
                            disabled={busy || completing}
                            onChange={(e) => editDraft(service.id, field.name, e.target.value)}
                          />
                        </div>
                      </div>
                    ))}
                  </div>
                  {service.validator ? (
                    <div className="admin-actions-row">
                      <button
                        type="button"
                        className="admin-btn admin-btn-ghost"
                        data-testid={`setup-validate-${service.id}`}
                        disabled={!hasValues || busy || completing || !backendUp}
                        onClick={() => {
                          void validateService(service);
                        }}
                      >
                        {busy ? "Checking..." : "Check"}
                      </button>
                    </div>
                  ) : null}
                  {verdict && (verdict.status === "invalid" || verdict.status === "error") ? <ErrorToast message={userErrorMessage(verdict.message, "Credential validation failed. Check the value and try again.")} testId={`setup-verdict-${service.id}`} /> : verdict ? (
                    <p
                      className="admin-card-note"
                      role="status"
                      data-testid={`setup-verdict-${service.id}`}
                    >
                      {userErrorMessage(verdict.message, verdict.status)}
                    </p>
                  ) : null}
                </div>
              );
            })}
          </section>
        ))
      )}

      <section className="admin-card preferences-group">
        <div className="admin-actions-row">
          <button
            type="button"
            className="admin-btn admin-btn-primary"
            data-testid="setup-complete"
            disabled={activity.kind !== "idle"}
            onClick={() => {
              void complete();
            }}
          >
            {completing ? "Storing..." : "Store everything and finish"}
          </button>
        </div>
        {completeError ? (
          <ErrorToast message={completeError} testId="setup-complete-error" />
        ) : null}
      </section>
    </main>
  );
}
