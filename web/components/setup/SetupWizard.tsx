"use client";

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

import { useCallback, useMemo, useState } from "react";
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
  const [busyService, setBusyService] = useState<string | null>(null);
  const [completing, setCompleting] = useState(false);
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
      setTokenError(error instanceof Error ? error.message : "token check failed");
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
      if (Object.keys(values).length === 0 || busyService) return;
      setBusyService(service.id);
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
            message: error instanceof Error ? error.message : "check failed",
          },
        }));
      } finally {
        setBusyService(null);
      }
    },
    [attempts, busyService, token, valuesFor],
  );

  const complete = useCallback(async () => {
    if (!registry || completing) return;
    const services: Record<string, Record<string, string>> = {};
    for (const service of registry.services) {
      const values = valuesFor(service);
      if (Object.keys(values).length > 0) services[service.id] = values;
    }
    if (Object.keys(services).length === 0) {
      setCompleteError("Nothing to save yet. Paste at least the Clerk and Turso credentials.");
      return;
    }
    setCompleting(true);
    setCompleteError(null);
    try {
      const data = await postJson<CompleteResponse>("/api/setup/complete", {
        token,
        services,
      });
      setResult(data);
      setStep("done");
    } catch (error) {
      setCompleteError(error instanceof Error ? error.message : "setup failed");
    } finally {
      setCompleting(false);
    }
  }, [completing, registry, token, valuesFor]);

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
            <span className="admin-card-title">Radon first-run setup</span>
          </header>
          <p className="preferences-row__description">
            No credentials are configured yet, so the whole app is parked here
            until you finish setup. Proof of ownership is the setup token
            printed in the terminal that launched Radon.
          </p>
          <div className="preferences-row__control">
            <input
              className="order-input preferences-row__input"
              type="password"
              autoComplete="off"
              placeholder="Paste the setup token"
              value={token}
              data-testid="setup-token-input"
              onChange={(e) => setToken(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") void submitToken();
              }}
            />
            <button
              type="button"
              className="admin-btn admin-btn-primary"
              data-testid="setup-token-submit"
              disabled={!token.trim() || tokenBusy}
              onClick={() => {
                void submitToken();
              }}
            >
              {tokenBusy ? "Checking..." : "Begin"}
            </button>
          </div>
          {tokenError ? (
            <p className="admin-card-note admin-card-error" role="alert" data-testid="setup-token-error">
              {tokenError}
            </p>
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
                {outcome.validation.message ? ` (${outcome.validation.message})` : ""}
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
        <p className="admin-card-note preferences-store-banner" role="status">
          The FastAPI backend is not answering yet, so live vendor checks are
          offline. You can still paste values and finish; they land in .env.
        </p>
      ) : null}
      {!registry ? (
        <section className="admin-card preferences-group">
          <p className="preferences-row__description">
            The credential registry is unavailable (backend offline). Start the
            stack and reload, or finish later from the profile page.
          </p>
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
                <div className="preferences-row" data-testid={`setup-service-${service.id}`} key={service.id}>
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
                  {service.fields.map((field) => (
                    <div className="preferences-row__control" key={field.name}>
                      <label className="preferences-row__meta" htmlFor={`setup-${field.name}`}>
                        <span className="preferences-row__head">
                          <span className="preferences-row__label">{field.label}</span>
                          <code className="preferences-row__key">{field.name}</code>
                        </span>
                      </label>
                      <input
                        id={`setup-${field.name}`}
                        className="order-input preferences-row__input"
                        type={field.secret ? "password" : "text"}
                        autoComplete="off"
                        spellCheck={false}
                        placeholder={field.placeholder || "Paste value"}
                        value={drafts[field.name] ?? ""}
                        disabled={busy || completing}
                        onChange={(e) =>
                          setDrafts((current) => ({ ...current, [field.name]: e.target.value }))
                        }
                      />
                    </div>
                  ))}
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
                  {verdict ? (
                    <p
                      className={
                        verdict.status === "invalid" || verdict.status === "error"
                          ? "admin-card-note admin-card-error"
                          : "admin-card-note"
                      }
                      role={verdict.status === "invalid" ? "alert" : "status"}
                      data-testid={`setup-verdict-${service.id}`}
                    >
                      {verdict.message || verdict.status}
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
            disabled={completing}
            onClick={() => {
              void complete();
            }}
          >
            {completing ? "Storing..." : "Store everything and finish"}
          </button>
        </div>
        {completeError ? (
          <p className="admin-card-note admin-card-error" role="alert" data-testid="setup-complete-error">
            {completeError}
          </p>
        ) : null}
      </section>
    </main>
  );
}
