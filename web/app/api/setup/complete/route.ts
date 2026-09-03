import { NextResponse } from "next/server";
import { radonFetch, RadonApiError } from "@/lib/radonApi";
import { getRequestId, jsonApiError, setNoStoreResponseHeaders } from "@/lib/apiContracts";
import { isSetupMode, isAuthMisconfigured } from "@/lib/setup/setupMode";
import { verifySetupToken, consumeSetupToken } from "@/lib/setup/setupToken";
import { markSetupComplete, resolveRepoRoot } from "@/lib/setup/setupComplete";
import { partitionEnvEncodable, writeSetupEnvFiles } from "@/lib/setup/envFiles";

/**
 * First-run wizard completion.
 *
 * For every service that carries values, stores them through FastAPI
 * PUT /credentials/{service} (vendor-validated, encrypted at rest, exported
 * live). Then materializes the collected values into the repo root .env and
 * web/.env so the restarted stack boots with Clerk + Turso configured. A
 * vendor-rejected service is reported back and NOT stored; everything else
 * proceeds — one bad key must not strand the whole onboarding.
 */

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const SERVICE_PATTERN = /^[a-z][a-z0-9_]{0,63}$/;
const FIELD_PATTERN = /^[A-Z][A-Z0-9_]{0,63}$/;
const STORE_TIMEOUT_MS = 100_000;

export const radonCapability = "internal";

type ServiceOutcome = {
  service: string;
  stored: boolean;
  validation: { status: string; message: string };
};

export async function POST(request: Request): Promise<Response> {
  const requestId = getRequestId();
  if (isAuthMisconfigured()) {
    return setNoStoreResponseHeaders(
      jsonApiError({
        message: "Setup already completed. Restart the stack to load authentication keys.",
        status: 403,
        code: "SETUP_ALREADY_COMPLETE",
        requestId,
      }),
      requestId,
    );
  }
  if (!isSetupMode()) {
    return setNoStoreResponseHeaders(
      jsonApiError({ message: "Not found", status: 404, code: "NOT_FOUND", requestId }),
      requestId,
    );
  }
  let body: { token?: unknown; services?: unknown };
  try {
    body = (await request.json()) as typeof body;
  } catch {
    body = {};
  }
  if (!verifySetupToken(body.token)) {
    return setNoStoreResponseHeaders(
      jsonApiError({
        message: "Setup token mismatch. It is printed in the terminal that launched Radon.",
        status: 401,
        code: "SETUP_TOKEN_INVALID",
        requestId,
      }),
      requestId,
    );
  }

  const services =
    body.services && typeof body.services === "object" && !Array.isArray(body.services)
      ? (body.services as Record<string, unknown>)
      : null;
  if (!services || Object.keys(services).length === 0) {
    return setNoStoreResponseHeaders(
      jsonApiError({ message: "services object is required", status: 400, code: "BAD_REQUEST", requestId }),
      requestId,
    );
  }

  const outcomes: ServiceOutcome[] = [];
  const collected: Record<string, string> = {};
  let backend = true;

  for (const [service, raw] of Object.entries(services)) {
    if (!SERVICE_PATTERN.test(service)) continue;
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) continue;
    const values: Record<string, string> = {};
    for (const [name, value] of Object.entries(raw as Record<string, unknown>)) {
      if (FIELD_PATTERN.test(name) && typeof value === "string" && value.trim()) {
        values[name] = value.trim();
      }
    }
    if (Object.keys(values).length === 0) continue;

    try {
      const data = (await radonFetch(`/credentials/${encodeURIComponent(service)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ values, updated_by: "setup-wizard" }),
        timeout: STORE_TIMEOUT_MS,
      })) as { validation?: { status?: string; message?: string } };
      outcomes.push({
        service,
        stored: true,
        validation: {
          status: data.validation?.status ?? "unchecked",
          message: data.validation?.message ?? "",
        },
      });
      Object.assign(collected, values);
    } catch (error) {
      if (error instanceof RadonApiError && error.status === 422) {
        const detail = (error.detail ?? {}) as { message?: string };
        outcomes.push({
          service,
          stored: false,
          validation: { status: "invalid", message: detail.message ?? "vendor rejected" },
        });
        continue;
      }
      // Backend unreachable: still write the env files so the restart can
      // bring the stack up; the store copy syncs on first save afterwards.
      backend = false;
      outcomes.push({
        service,
        stored: false,
        validation: { status: "error", message: "backend unreachable; written to .env only" },
      });
      Object.assign(collected, values);
    }
  }

  let written: string[] = [];
  const repoRoot = resolveRepoRoot(process.cwd());
  if (!repoRoot) {
    return setNoStoreResponseHeaders(
      jsonApiError({
        message: `Cannot resolve repo root from ${process.cwd()}`,
        status: 500,
        code: "SETUP_REPO_ROOT_INVALID",
        requestId,
      }),
      requestId,
    );
  }
  // REL-216 (R-591): a value the env writer refuses is dropped and reported,
  // never thrown — the credentials are already stored in the encrypted store,
  // and an un-latched setup retries into the identical 500 forever.
  const { encodable, refused } = partitionEnvEncodable(collected);
  for (const { key, message } of refused) {
    outcomes.push({
      service: `env:${key}`,
      stored: false,
      validation: { status: "env_refused", message },
    });
  }
  if (Object.keys(encodable).length > 0) {
    written = await writeSetupEnvFiles(encodable, repoRoot);
  }
  await markSetupComplete(repoRoot);
  consumeSetupToken();

  return setNoStoreResponseHeaders(
    NextResponse.json({
      ok: true,
      backend,
      outcomes,
      written,
      restart_required: true,
    }),
    requestId,
  );
}
