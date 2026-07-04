// Pins the factual claims made on /privacy against the site source itself.
// The privacy page states: no cookies, no sessionStorage, and exactly one
// localStorage key ("theme") written from exactly one user-initiated place.
// If anyone adds a tracker, a cookie write, or a new storage key, these
// tests break loudly and the privacy copy must be revisited first.

import { readdirSync, readFileSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { legalPages } from "./legal-pages";
import { privacySections } from "./pages/privacy";
import { SITE_THEME_STORAGE_KEY } from "./theme";

const siteRoot = join(dirname(fileURLToPath(import.meta.url)), "..");

// Shipped site source only: e2e specs and tests never reach the browser.
const SOURCE_DIRS = ["app", "components", "lib"];
const SOURCE_EXTENSIONS = [".ts", ".tsx"];
const TEST_FILE_PATTERN = /\.(test|spec)\.tsx?$/;

// The only files allowed to mention localStorage: the theme bootstrap
// (read), the theme toggle (the single user-initiated write), and the
// privacy page copy that discloses exactly that inventory.
const APPROVED_LOCAL_STORAGE_FILES = new Set([
  "lib/theme.ts",
  "components/atoms/ThemeToggle.tsx",
  "lib/pages/privacy.ts",
]);

function collectSourceFiles(directory: string, found: string[] = []): string[] {
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    const entryPath = join(directory, entry.name);
    if (entry.isDirectory()) {
      collectSourceFiles(entryPath, found);
      continue;
    }
    const isSource = SOURCE_EXTENSIONS.some((ext) => entry.name.endsWith(ext));
    if (isSource && !TEST_FILE_PATTERN.test(entry.name)) {
      found.push(entryPath);
    }
  }
  return found;
}

const sourceFiles = SOURCE_DIRS.flatMap((dir) =>
  collectSourceFiles(join(siteRoot, dir)),
);

function filesContaining(needle: string): string[] {
  return sourceFiles
    .filter((file) => readFileSync(file, "utf8").includes(needle))
    .map((file) => relative(siteRoot, file));
}

describe("privacy-page claims hold against the site source", () => {
  it("collected a plausible source inventory", () => {
    expect(sourceFiles.length).toBeGreaterThan(20);
  });

  it("sets no cookies anywhere in shipped source", () => {
    expect(filesContaining("document.cookie")).toEqual([]);
    expect(filesContaining("cookieStore")).toEqual([]);
  });

  it("uses no sessionStorage anywhere in shipped source", () => {
    expect(filesContaining("sessionStorage")).toEqual([]);
  });

  it("confines localStorage to the user-initiated theme preference", () => {
    const localStorageFiles = filesContaining("localStorage");
    expect(new Set(localStorageFiles)).toEqual(APPROVED_LOCAL_STORAGE_FILES);
    // The single key the privacy page discloses.
    expect(SITE_THEME_STORAGE_KEY).toBe("theme");
    // The only write is the toggle click handler; the bootstrap only reads.
    const themeSource = readFileSync(join(siteRoot, "lib/theme.ts"), "utf8");
    expect(themeSource).not.toContain("localStorage.setItem");
  });

  it("states the no-banner verdict and the storage inventory on the page", () => {
    const copy = privacySections
      .flatMap((section) => section.paragraphs)
      .join(" ");
    expect(copy).toContain("radon.run sets no cookies");
    expect(copy).toContain("consent banner");
    expect(copy).toContain(`"${SITE_THEME_STORAGE_KEY}"`);
    expect(copy).toContain("Vercel Web Analytics");
  });

  it("keeps legal copy free of em dashes", () => {
    for (const file of ["lib/pages/privacy.ts", "lib/pages/terms.ts"]) {
      const source = readFileSync(join(siteRoot, file), "utf8");
      expect(source.includes("—"), `${file} contains an em dash`).toBe(
        false,
      );
    }
  });

  it("registers both legal routes for the sitemap and footer", () => {
    expect(legalPages.map((page) => page.slug)).toEqual(["privacy", "terms"]);
    for (const page of legalPages) {
      expect(page.title.endsWith("| Radon Terminal")).toBe(true);
      expect(page.title.length).toBeLessThanOrEqual(60);
      expect(Number.isNaN(new Date(page.lastModified).getTime())).toBe(false);
    }
  });
});
