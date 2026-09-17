import { describe, expect, it } from "vitest";
import { aiMetricDescription, aiMetricDisplay, aiMetricLabel } from "@/lib/aiMetricPresentation";
import { aiFixture } from "./fixtures/aiInfrastructure";
const metric = { ...aiFixture.indicators[0].metrics[0], source_id: "openrouter", id: "other_share", label: "other share", value: 0.067, unit: "ratio" };
describe("AI observation language", () => {
  it("defines residual traffic without claiming paid usage", () => {
    expect(aiMetricLabel(metric)).toBe("Unlisted models' share of tokens");
    expect(aiMetricDisplay(metric)).toEqual({ value: "6.7", unit: "%" });
    expect(aiMetricDescription({ ...metric, id: "other" })).toContain("Free versus paid usage is not disclosed");
    expect(aiMetricDescription({ ...metric, id: "visible_nonfree_tokens" })).toContain("does not establish actual payment");
  });
  it("gives historical series the same name without leaking methodology IDs", () => {
    expect(aiMetricLabel({ label: "other share", source_id: "openrouter", unit: "ratio", series_id: "other_share:ai-cycle-v2:publisher-v1:1" })).toBe(aiMetricLabel(metric));
    expect(aiMetricLabel({ ...metric, id: "total_tokens" })).toBe("Total routed tokens");
  });
  it("formats growth as a percentage without mutating stored ratios or unrelated sources", () => {
    expect(aiMetricDisplay({ ...metric, id: "growth_28d", value: -0.125 })).toEqual({ value: "-12.5", unit: "%" });
    expect(aiMetricDisplay({ ...metric, value: null })).toEqual({ value: "Unavailable", unit: "%" });
    expect(aiMetricDisplay({ ...metric, source_id: "sec" })).toEqual({ value: "0.067", unit: "ratio" });
    expect(metric.value).toBe(0.067);
  });
  it("preserves model version and publication date in readable labels", () => {
    expect(aiMetricLabel({ ...metric, id: "anthropic/claude-4.6-sonnet-20260217", label: "anthropic/claude-4.6-sonnet-20260217" })).toBe("Claude Sonnet 4.6 (2026-02-17)");
  });
});
