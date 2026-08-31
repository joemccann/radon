/**
 * /admin is the operator control panel on app.radon.run.
 * DEMO_ADMIN_USER_IDS is the demo-trial manager list and is unset in
 * production. Gating the page on that env 404s the signed-in operator.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const WEB_ROOT = fileURLToPath(new URL("..", import.meta.url));

function source(rel: string): string {
  return readFileSync(resolve(WEB_ROOT, rel), "utf8");
}

const OPERATOR_ADMIN_APIS = [
  "app/api/admin/services/route.ts",
  "app/api/admin/services/[unit]/[action]/route.ts",
  "app/api/admin/ib/restart/route.ts",
  "app/api/admin/ib/reset-backoff/route.ts",
  "app/api/admin/stack/restart/route.ts",
  "app/api/preferences/route.ts",
] as const;

describe("operator admin page gate", () => {
  it("uses ALLOWED_USER_IDS / requireRouteAccess, not DEMO_ADMIN_USER_IDS", () => {
    const text = source("app/admin/page.tsx");
    expect(text).toContain("requireRouteAccess");
    expect(text).toContain("operatorOnly: true");
    expect(text).not.toContain("requireDemoAdmin");
  });

  it("keeps demo-user management on the demo-admin allowlist", () => {
    const text = source("app/api/admin/demo-users/route.ts");
    expect(text).toContain("requireDemoAdmin");
  });

  it("gates IB and service control APIs on the operator allowlist", () => {
    for (const rel of OPERATOR_ADMIN_APIS) {
      const text = source(rel);
      expect(text, rel).toContain("requireRouteAccess");
      expect(text, rel).toContain("operatorOnly: true");
      expect(text, rel).not.toContain("requireDemoAdmin");
    }
  });
});
