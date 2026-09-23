import { describe, expect, it } from "vitest";
import { AI_INDUSTRY_STAGES, AI_MEASURE_COPY, industryStage, measureCopy } from "@/lib/aiIndustryPresentation";
import { aiFixture } from "./fixtures/aiInfrastructure";

describe("AI Industry information architecture", () => {
  it("assigns 18 industry measures once, with M1 a cross-cutting handoff", () => {
    const ids = AI_INDUSTRY_STAGES.flatMap(stage => [...stage.ids]);
    expect(ids).toHaveLength(18);
    expect(new Set(ids).size).toBe(18);
    expect(Object.keys(AI_MEASURE_COPY)).toHaveLength(19);
    expect(industryStage({ ...aiFixture.indicators[0], id: "D6" })).toBe("capability");
    expect(industryStage({ ...aiFixture.indicators[0], id: "M1" })).toBe("market");
    expect(industryStage({ ...aiFixture.indicators[0], id: "S1" })).toBe("buildout");
    expect(AI_MEASURE_COPY.S1.definition).toContain("Source: Epoch AI");
    expect(AI_MEASURE_COPY.S1.limit).toContain("Source: Epoch AI");
    expect(AI_MEASURE_COPY.S1.relevance.toLowerCase()).toMatch(/research|context/);
  });
  it("uses registry methodology for unknown measures instead of inventing a definition", () => {
    const indicator = { ...aiFixture.indicators[0], id: "D99" };
    expect(industryStage(indicator)).toBe("adoption");
    expect(measureCopy(indicator).definition).toBe(indicator.methodology);
    expect(measureCopy(indicator).limit).toBe(indicator.reason);
  });
  it("keeps snapshot-specific numeric claims out of evergreen copy", () => {
    expect(JSON.stringify(AI_MEASURE_COPY)).not.toMatch(/Illustrative|audited snapshot|only one date|No observations are available/);
  });
});
