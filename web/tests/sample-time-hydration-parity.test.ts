import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

import { formatSampleTime } from "@/lib/sampleTime";

/**
 * React #418 on /portfolio (2026-10-15): the page is server-rendered from a
 * seeded snapshot, and the shell's "Last sample" label formatted `last_sync`
 * in the RUNTIME's zone. The server (UTC) and the browser (ET) printed
 * different text, so hydration failed on every load.
 */
describe("formatSampleTime", () => {
  it("renders the sample in ET, not the runtime zone", () => {
    // 14:16:24Z is 10:16:24 EDT regardless of where this runs.
    expect(formatSampleTime("2026-10-15T14:16:24Z")).toBe("10:16:24");
  });

  it("renders --- for missing or unparseable timestamps", () => {
    expect(formatSampleTime(null)).toBe("---");
    expect(formatSampleTime(undefined)).toBe("---");
    expect(formatSampleTime("not-a-date")).toBe("---");
  });
});

describe("server-rendered sample labels", () => {
  const source = (rel: string) => readFileSync(path.join(__dirname, "..", rel), "utf8");

  it.each(["components/WorkspaceShell.tsx", "components/Header.tsx"])(
    "%s formats the sample through the zone-pinned helper",
    (file) => {
      const code = source(file);
      expect(code).toContain("formatSampleTime(");
      expect(code).not.toMatch(/toLocaleTimeString\(\[\]/);
    },
  );
});
