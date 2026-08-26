/**
 * @vitest-environment node
 *
 * REL-092 — a panel must be able to say "this failed".
 *
 * R-245: the vol-cone GET contract is HTTP 200 with `missing: true`
 * (`web/lib/volCone.ts`). `ScannerHero` never reads it — the cone branch tests
 * loading, error, then `!hits?.length` — so a total outage paints as a
 * completed scan that found nothing, and the meta rail reinforces it because
 * `?? null` only fires on `undefined`, leaving `scanned`/`candidates` at 0
 * rather than "—". `VolConePanel` DOES check `missing` for the same payload,
 * so the two surfaces disagree about the same outage.
 *
 * R-246: `VolConePanel` never destructures `error`, though `UseSyncReturn`
 * exposes it. A permanently failing route leaves `data` null forever and the
 * component falls into an empty state reading "Data appears after the first
 * successful pull" — a fetch fault rendered verbatim as a benign
 * pre-population state, with no path that can display an error at all.
 *
 * R-247: `if (error && !data)` is the only consumer of `error`, so once a
 * payload has ever loaded a sustained failure leaves the full populated panel
 * on screen with a freshness badge derived entirely from the stale cached
 * payload. Same shape in `OptionsExposurePanel`.
 *
 * R-272: `coneFillPct` returns 0 both for "cone bounds unavailable" and for
 * "sitting at or above the p90 ceiling" — and the bar reads longer-is-better,
 * so an uncomputable cone renders as maximally rich.
 *
 * R-273: `finiteBrushValue` maps a null `atm_iv` to 0 and feeds the brush,
 * plotting a missing session as a 0% implied-vol floor spike — while the type
 * comment says "Nulls are preserved for chart gaps" and the main chart honours
 * that.
 */

import { readFileSync } from "fs";
import { join } from "path";
import { describe, expect, it } from "vitest";

import { coneFillPct } from "../lib/scannerHero";

const ROOT = join(__dirname, "..");

function source(rel: string): string {
  return readFileSync(join(ROOT, rel), "utf-8")
    .split("\n")
    .filter((line) => {
      const t = line.trim();
      return !t.startsWith("//") && !t.startsWith("*") && !t.startsWith("/*") && !t.startsWith("{/*");
    })
    .join("\n");
}

describe("ScannerHero reads the missing flag", () => {
  it("branches on cone.data.missing", () => {
    const src = source("components/dashboard/ScannerHero.tsx");
    expect(src).toContain("cone.data?.missing");
  });

  it("does not report a scanned/candidate count for a missing payload", () => {
    const src = source("components/dashboard/ScannerHero.tsx");
    expect(src).toMatch(/coneMissing/);
  });
});

describe("VolConePanel can render a fault", () => {
  it("destructures error from the sync hook", () => {
    const src = source("components/VolConePanel.tsx");
    expect(src).toMatch(/const \{[^}]*\berror\b[^}]*\} = useVolCone\(\)/);
  });

  it("has a branch that shows the error text", () => {
    const src = source("components/VolConePanel.tsx");
    expect(src).toMatch(/if \(error[^)]*\)/);
  });
});

describe("Panels surface a failed refresh behind a cached payload", () => {
  it("GammaRotationBody receives the error", () => {
    const src = source("components/GammaRotationPanel.tsx");
    expect(src).toMatch(/GammaRotationBody data=\{data\}[^>]*refreshFailed=\{error\}/);
  });

  it("OptionsExposurePanel does not drop error once data exists", () => {
    const src = source("components/OptionsExposurePanel.tsx");
    expect(src).not.toMatch(/if \(error && !data\)[\s\S]{0,400}?return[\s\S]{0,4000}$/);
    expect(src).toContain("refreshFailed");
  });
});

describe("coneFillPct distinguishes unavailable from rich", () => {
  it("returns null when the cone bounds cannot be computed", () => {
    expect(coneFillPct({ atm_iv: 20, p10: null, p90: null })).toBeNull();
    expect(coneFillPct({ atm_iv: null, p10: 10, p90: 30 })).toBeNull();
    // Degenerate cone.
    expect(coneFillPct({ atm_iv: 20, p10: 30, p90: 30 })).toBeNull();
  });

  it("still returns 0 for a name at the p90 ceiling", () => {
    expect(coneFillPct({ atm_iv: 30, p10: 10, p90: 30 })).toBe(0);
  });

  it("returns 100 on the cone floor", () => {
    expect(coneFillPct({ atm_iv: 10, p10: 10, p90: 30 })).toBe(100);
  });
});

describe("the brush minimap keeps gaps as gaps", () => {
  it("does not floor a null atm_iv to zero", () => {
    const src = source("components/VolConePanel.tsx");
    expect(src).not.toContain("function finiteBrushValue(value: number | null | undefined): number {");
    expect(src).toMatch(/brushValue[\s\S]{0,200}null/);
  });
});
