/**
 * @vitest-environment node
 *
 * VIXCOR — the rebuttal may not over-claim in the opposite direction.
 *
 * The shipped sentence
 *
 *   "Forward VIX drawup after a breakdown is below the all-session base rate
 *    at every horizon."
 *
 * is true of the MEAN only. On the MEDIAN the ordering reverses at 42
 * sessions, which is exactly the horizon the operator's four blue arrows span
 * ("a higher VIX inside two months"):
 *
 *   h=42  event mean   0.3301  <  base mean   0.4401     (mean is below, as claimed)
 *   h=42  event median 0.3179  >  base median 0.2931     (median is ABOVE)
 *         0.31786580800443087 - 0.2930825885883874 = +0.02478321941604347 = +2.48pp
 *   h=42  P(drawup >= +20%)  0.6333 event vs 0.6380 base  (indistinguishable)
 *   Mann-Whitney at h=42: p = 0.683, so "no difference" is the defensible read.
 *
 * The base mean is right-skewed by ~1.50x (mean / median), which is what
 * manufactures the mean-only gap. Overstating the refutation is the same sin
 * as overstating the claim, so the copy must name the MEAN and concede the
 * median at 42 sessions.
 *
 * This file pins the corrected wording at its TypeScript sites by reading the
 * sources, because two of the four occurrences are module docstrings that no
 * render can reach. The Python site (scripts/lib/vixcor_math.py) and the spec
 * site (docs/indicators/vixcor.md section 0) are pinned in
 * scripts/tests/test_vixcor.py.
 *
 * Spec: docs/indicators/vixcor.md sections 0, G.5, G.6.
 */
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const REPO_ROOT = join(__dirname, "..");

const PANEL = "components/VixCorPanel.tsx";
const LIB = "lib/vixcor.ts";

/**
 * Collapse the wrapping so a prettier reflow cannot break a copy pin. Leading
 * JSDoc asterisks go first, or a sentence wrapped inside a block comment reads
 * as "... at * every horizon" and slips past the ban.
 */
function normalized(text: string): string {
  return text.replace(/^\s*\*\s?/gm, " ").replace(/\s+/g, " ");
}

/** The claim that is only true of the mean. Must not survive anywhere. */
const OVER_CLAIMS: ReadonlyArray<RegExp> = [
  /below the all-session base rate at every horizon/i,
  /runs below the all-session base rate at every horizon/i,
  /below the unconditional base rate at every horizon/i,
];

/** The two halves the corrected sentence must carry at every site. */
const QUALIFIED_MEAN = /below the all-session mean at every horizon/i;
const QUALIFIED_MEDIAN =
  /on the median the two are indistinguishable at 42 sessions/i;

async function source(relative: string): Promise<string> {
  return normalized(await readFile(join(REPO_ROOT, relative), "utf8"));
}

describe.each([PANEL, LIB])("%s — the rebuttal is scoped to the mean", (file) => {
  it("drops the unqualified every-horizon claim", async () => {
    const text = await source(file);
    for (const claim of OVER_CLAIMS) {
      expect(text).not.toMatch(claim);
    }
  });

  it("names the mean and concedes the median at 42 sessions", async () => {
    const text = await source(file);
    expect(text).toMatch(QUALIFIED_MEAN);
    expect(text).toMatch(QUALIFIED_MEDIAN);
  });
});

describe("VixCorPanel — the user-visible strings carry the qualification", () => {
  it("qualifies the base-rate footnote and keeps the not-a-forecast label", async () => {
    const text = await source(PANEL);
    expect(text).toMatch(
      /Forward VIX drawup after a breakdown is below the all-session mean at every horizon[^"]*on the median the two are indistinguishable at 42 sessions[^"]*This is a regime description, not a forecast\./i,
    );
  });

  it("qualifies the tooltip too, and still lands on a regime state", async () => {
    const text = await source(PANEL);
    const tooltip = text.match(/const VIXCOR_TOOLTIP\s*=\s*"([^"]*)"/);
    expect(tooltip, "VIXCOR_TOOLTIP must be a single double-quoted literal").toBeTruthy();
    const copy = tooltip![1];
    expect(copy).toMatch(QUALIFIED_MEAN);
    expect(copy).toMatch(QUALIFIED_MEDIAN);
    for (const claim of OVER_CLAIMS) {
      expect(copy).not.toMatch(claim);
    }
  });

  it("adds no em dash to the copy constants while qualifying", async () => {
    const text = await source(PANEL);
    for (const name of ["VIXCOR_TOOLTIP", "BASE_RATE_FOOTNOTE"]) {
      const literal = text.match(new RegExp(`const ${name}\\s*=\\s*"([^"]*)"`));
      expect(literal, `${name} must be a single double-quoted literal`).toBeTruthy();
      expect(literal![1]).not.toContain("—");
    }
  });
});
