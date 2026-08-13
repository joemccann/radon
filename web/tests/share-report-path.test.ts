import { describe, it, expect } from "vitest";
import { join } from "path";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { resolve } from "node:path";
import { isAllowedShareCardPath, REPORTS_DIR } from "@/lib/shareReportPath";

describe("isAllowedShareCardPath", () => {
  it("allows the generator's own share-card filenames", () => {
    expect(isAllowedShareCardPath(join(REPORTS_DIR, "tweet-gex-2026-06-28.html"), "gex")).toBe(true);
    expect(isAllowedShareCardPath(join(REPORTS_DIR, "tweet-gex-2026-06-28-card-3.html"), "gex")).toBe(true);
    expect(isAllowedShareCardPath(join(REPORTS_DIR, "tweet-cta-2026-03-19-card-1.html"), "cta")).toBe(true);
    expect(isAllowedShareCardPath(join(REPORTS_DIR, "tweet-vcg-2026-06-28.html"), "vcg")).toBe(true);
  });

  it("BLOCKS operator-private reports (the disclosure bug)", () => {
    expect(isAllowedShareCardPath(join(REPORTS_DIR, "portfolio-2026-03-09.html"), "gex")).toBe(false);
    expect(isAllowedShareCardPath(join(REPORTS_DIR, "aapl-evaluation-2026-06-27.html"), "gex")).toBe(false);
    expect(isAllowedShareCardPath(join(REPORTS_DIR, "daily-scan-2026-06-28.html"), "gex")).toBe(false);
    expect(isAllowedShareCardPath(join(REPORTS_DIR, "garch-convergence-2026-06-28.html"), "vcg")).toBe(false);
    expect(isAllowedShareCardPath(join(REPORTS_DIR, "trade-recommendations-2026-06-28.html"), "regime")).toBe(false);
  });

  it("blocks cross-type card access (gex route cannot serve a cta card)", () => {
    expect(isAllowedShareCardPath(join(REPORTS_DIR, "tweet-cta-2026-03-19.html"), "gex")).toBe(false);
    expect(isAllowedShareCardPath(join(REPORTS_DIR, "tweet-gex-2026-06-28.html"), "cta")).toBe(false);
  });

  it("blocks path traversal and absolute paths outside reports/", () => {
    expect(isAllowedShareCardPath(join(REPORTS_DIR, "../data/portfolio.json"), "gex")).toBe(false);
    expect(isAllowedShareCardPath(join(REPORTS_DIR, "..", "..", "etc", "passwd"), "gex")).toBe(false);
    expect(isAllowedShareCardPath("/etc/passwd", "gex")).toBe(false);
    expect(isAllowedShareCardPath(`${REPORTS_DIR}-secrets/tweet-gex-2026-06-28.html`, "gex")).toBe(false);
  });

  it("blocks nested subdirectories under reports/", () => {
    expect(isAllowedShareCardPath(join(REPORTS_DIR, "private", "tweet-gex-2026-06-28.html"), "gex")).toBe(false);
  });

  it("rejects missing/empty path", () => {
    expect(isAllowedShareCardPath(null, "gex")).toBe(false);
    expect(isAllowedShareCardPath("", "gex")).toBe(false);
    expect(isAllowedShareCardPath(undefined, "gex")).toBe(false);
  });

  it("rejects non-html and malformed card names", () => {
    expect(isAllowedShareCardPath(join(REPORTS_DIR, "tweet-gex-2026-06-28.png"), "gex")).toBe(false);
    expect(isAllowedShareCardPath(join(REPORTS_DIR, "tweet-gex-badd.html"), "gex")).toBe(false);
    expect(isAllowedShareCardPath(join(REPORTS_DIR, "tweet-gex-2026-06-28.html.bak"), "gex")).toBe(false);
  });
});

it("share actions are parent owned and iframe remains fully isolated", () => {
  const webRoot = fileURLToPath(new URL("..", import.meta.url));
  const source = readFileSync(resolve(webRoot, "components/ShareReportModal.tsx"), "utf8");
  expect(source).toContain('download="radon-share-preview.html"');
  expect(source).toContain('sandbox=""');
  expect(source).not.toContain('sandbox="allow-scripts"');
});
