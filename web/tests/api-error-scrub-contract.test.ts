/**
 * @vitest-environment node
 *
 * F20260917-C03: scrubSecrets chokepoint bypass. jsonApiError scrubs error
 * text, but routes that hand a caught error's `.message` straight to
 * NextResponse.json skip the chokepoint and can leak secret-bearing upstream
 * error strings (LibsqlError URL + token class) to non-operator users.
 *
 * Static contract: in every file under web/app/api, any variable assigned
 * from `<err> instanceof Error ? <err>.message : ...` must be wrapped in
 * scrubSecrets at the assignment if it flows into a NextResponse.json body
 * (error / detail / message field, directly or via template interpolation).
 * jsonApiError / jsonError call sites are exempt — they scrub internally.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";

const API_ROOT = path.join(__dirname, "..", "app", "api");

function walk(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) return walk(full);
    return entry.name.endsWith(".ts") ? [full] : [];
  });
}

/** Files where a raw err.message flows into a NextResponse.json body. */
function findOffenders(): string[] {
  const offenders: string[] = [];
  for (const file of walk(API_ROOT)) {
    const src = readFileSync(file, "utf8");
    // Vars assigned from a caught error's message WITHOUT scrubSecrets.
    const rawAssignments = [
      ...src.matchAll(/(?:const|let)\s+(\w+)\s*=\s*([^;\n]*instanceof \w*Error[^;\n]*\.message[^;\n]*)/g),
    ].filter(([, , rhs]) => !rhs.includes("scrubSecrets"));
    const rawVars = rawAssignments.map(([, name]) => name);
    // NextResponse.json bodies (object literal up to 400 chars).
    const jsonBodies = [...src.matchAll(/NextResponse\.json\(\s*\{[\s\S]{0,400}?\}\s*[,)]/g)].map(
      (m) => m[0],
    );
    for (const body of jsonBodies) {
      if (body.includes("scrubSecrets")) continue;
      const leaks =
        rawVars.some((v) =>
          new RegExp(`(?:error|detail|message)\\s*:\\s*(?:${v}\\b|\`[^\`]*\\$\\{${v}\\b)`).test(body),
        ) ||
        /(?:error|detail|message)\s*:\s*\w+ instanceof \w*Error \? [^,\n]*\.message/.test(body);
      if (leaks) {
        offenders.push(path.relative(API_ROOT, file));
        break;
      }
    }
  }
  return offenders.sort();
}

describe("F20260917-C03: no web API route returns a raw err.message", () => {
  it("every err.message flowing into NextResponse.json is scrubbed", () => {
    expect(findOffenders()).toEqual([]);
  });
});

/* Behavioral leg: a formerly-offending route redacts a secret-shaped message. */
const mocks = vi.hoisted(() => ({ radonFetch: vi.fn() }));
vi.mock("@/lib/radonApi", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/radonApi")>();
  return { ...actual, radonFetch: mocks.radonFetch };
});
vi.mock("@/lib/routeAccess", () => ({
  requireRouteAccess: vi.fn(async () => ({ ok: true, principal: { userId: "u", kind: "operator" } })),
}));

// Low-entropy on purpose — a realistic token would trip the gitleaks CI gate.
const LEAKY = "connect failed: libsql://radon-secret.turso.io?authToken=fake-token-fake-token-fake";

describe("F20260917-C03: cash-flows route scrubs upstream error text", () => {
  beforeEach(() => {
    vi.resetModules();
    mocks.radonFetch.mockReset();
    mocks.radonFetch.mockRejectedValue(new Error(LEAKY));
  });

  it("GET /api/cash-flows redacts the DB URL in the error payload", async () => {
    const { GET } = await import("@/app/api/cash-flows/route");
    const res = await GET(new Request("http://localhost/api/cash-flows") as never);
    expect(res.status).toBe(502);
    const body = (await res.json()) as { error?: string };
    expect(body.error ?? "").not.toContain("libsql://");
    expect(body.error ?? "").not.toContain("turso.io");
    expect(body.error).toContain("[redacted-db-url]");
  });
});
