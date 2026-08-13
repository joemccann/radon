import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const WEB_ROOT = fileURLToPath(new URL("..", import.meta.url));
const source = (path: string) => readFileSync(resolve(WEB_ROOT, path), "utf8");

describe("production UI authentication cannot be disabled by public flags", () => {
  it("contains no runtime authless branches in production components", () => {
    for (const path of [
      "components/ClerkThemeBridge.tsx",
      "components/DemoWelcomeModal.tsx",
      "components/SignOutCachePurge.tsx",
      "components/mobile/MobileAppBar.tsx",
      "components/mobile/MobileMoreDrawer.tsx",
    ]) {
      const text = source(path);
      expect(text, path).not.toContain("NEXT_PUBLIC_RADON_AUTHLESS_TEST");
      expect(text, path).not.toContain("RADON_AUTHLESS_TEST");
    }
  });

  it("keeps browser authless testing server-only and token-bound", () => {
    const config = source("playwright.config.ts");
    expect(config).not.toContain("NEXT_PUBLIC_RADON_AUTHLESS_TEST");
    expect(config).toContain("RADON_AUTHLESS_TEST_TOKEN");
  });
});
