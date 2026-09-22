// @vitest-environment jsdom
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { AiInfrastructureView } from "@/components/AiInfrastructurePanel";
import { aiFixture } from "./fixtures/aiInfrastructure";

vi.mock("@/components/AiIndustryHistoryChart", () => ({ default: () => <div>History chart</div> }));
vi.mock("@/components/LlmTokenIndexCard", () => ({ default: () => <div>Legacy chart</div> }));
afterEach(cleanup);

const observations = [
  ["growth_28d", "growth 28d", 0.712, "ratio", "Token growth · 28 days"],
  ["mean_7d", "mean 7d", 17.856e12, "tokens", "Average daily tokens · 7 days"],
  ["other_share", "other share", 0.067, "ratio", "Unlisted models' share of tokens"],
  ["sum_28d", "sum 28d", 464.739e12, "tokens", "Total tokens · 28 days"],
  ["total_tokens", "total tokens", 18.403e12, "tokens", "Total routed tokens"],
  ["visible_nonfree_tokens", "visible nonfree tokens", 15.821e12, "tokens", "Tokens from listed non-free models"],
  ["anthropic/claude-4.6-sonnet-20260217", "anthropic/claude-4.6-sonnet-20260217", 62.056e9, "tokens", "Claude Sonnet 4.6 (2026-02-17)"],
  ["anthropic/claude-4.8-opus-20260528", "anthropic/claude-4.8-opus-20260528", 114.369e9, "tokens", "Claude Opus 4.8 (2026-05-28)"],
] as const;

function renderObservations() {
  const base = aiFixture.indicators[0];
  const metrics = observations.map(([id, label, value, unit]) => ({ ...base.metrics[0], id, label, value, unit, source_id: "openrouter" }));
  render(<AiInfrastructureView data={{ ...aiFixture, indicators: [{ ...base, metrics }] }} error={null} loading={false} refresh={() => {}} />);
  const details = screen.getByText("Latest reported observations (8)").closest("details")!;
  fireEvent.click(within(details).getByText("Latest reported observations (8)"));
  details.open = true;
  return details;
}

describe("AI observation explanations", () => {
  it.each(observations)("explains %s with a named keyboard-accessible info bubble", (id, _rawLabel, _value, _unit, label) => {
    const details = renderObservations();
    expect(within(details).getAllByTestId("ai-observation-card")).toHaveLength(8);
    const trigger = within(details).getByTestId(`ai-observation-info-${id}`);
    const button = within(trigger).getByRole("button", { name: `About ${label}` });
    fireEvent.focus(button);
    const tooltip = within(trigger).getByTestId(`ai-observation-description-${id}`);
    expect(tooltip.getAttribute("role")).toBe("tooltip");
    expect(tooltip.textContent?.length).toBeGreaterThan(80);
    expect(button.getAttribute("aria-describedby")).toBe(tooltip.id);
    fireEvent.keyDown(button, { key: "Escape" });
    expect(within(trigger).queryByTestId(`ai-observation-description-${id}`)).toBeNull();
  });
  it("shows understandable percentage values and preserves model identity in explanations", () => {
    const details = renderObservations();
    const growth = within(details).getByRole("button", { name: "About Token growth · 28 days" }).closest("div")!;
    const share = within(details).getByRole("button", { name: "About Unlisted models' share of tokens" }).closest("div")!;
    expect(growth.textContent).toContain("71.2 %");
    expect(share.textContent).toContain("6.7 %");
    fireEvent.focus(within(details).getByRole("button", { name: "About Claude Sonnet 4.6 (2026-02-17)" }));
    expect(screen.getByRole("tooltip").textContent).toContain("anthropic/claude-4.6-sonnet-20260217");
  });
});
