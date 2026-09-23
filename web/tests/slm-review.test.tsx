// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import SlmReview from "../app/admin/slm-review/slm-review";

function packet() {
  return {
    schema: "radon.slm-review.v1",
    runId: "fixture-run",
    taxonomy: ["OPTIONS", "GAMMA", "VOL"],
    items: Array.from({ length: 200 }, (_, index) => ({
      id: `post-${index}`,
      month: index < 100 ? "2026-08" : "2026-09",
      title: "Synthetic held-out title",
      text: "Synthetic source material, not a licensed post.",
      imageUrls: index < 40
        ? index === 0
          ? ["https://images.example.invalid/synthetic.png", "https://second.example.invalid/other.png"]
          : ["https://images.example.invalid/synthetic.png"]
        : [],
      candidates: {
        "Candidate 1": ["OPTIONS", "GAMMA", "VOL"],
        "Candidate 2": ["OPTIONS", "GAMMA", "VOL"],
        "Candidate 3": ["OPTIONS", "GAMMA", "VOL"],
      },
    })),
  };
}

describe("private SLM blind review", () => {
  afterEach(() => cleanup());
  beforeEach(() => localStorage.clear());

  it("keeps candidates hidden until human labels are locked and persists only decisions", async () => {
    render(<SlmReview reviewer="operator-fixture" />);
    const file = new File([JSON.stringify(packet())], "review-packet.json", { type: "application/json" });
    fireEvent.change(screen.getByLabelText("Load review packet", { selector: 'input[type="file"]' }), { target: { files: [file] } });
    await screen.findByText("Synthetic held-out title");
    expect(screen.queryByText("Candidate 1")).toBeNull();
    expect(screen.getByRole("button", { name: "Load 1 image from images.example.invalid" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Load 1 image from images.example.invalid" }));
    expect(screen.getByAltText("Post image from images.example.invalid, 1")).toBeTruthy();
    expect(screen.queryByAltText("Post image from second.example.invalid, 1")).toBeNull();
    expect(screen.getByRole("button", { name: "Load 1 image from second.example.invalid" })).toBeTruthy();

    fireEvent.change(screen.getByLabelText("TAG 1"), { target: { value: "OPTIONS" } });
    fireEvent.change(screen.getByLabelText("TAG 2"), { target: { value: "GAMMA" } });
    fireEvent.change(screen.getByLabelText("TAG 3"), { target: { value: "VOL" } });
    fireEvent.click(screen.getByRole("button", { name: "Lock labels and compare" }));
    expect(await screen.findByText("Candidate 1")).toBeTruthy();

    const candidate = screen.getByText("Candidate 1").parentElement;
    expect(candidate).not.toBeNull();
    fireEvent.click(within(candidate as HTMLElement).getByRole("button", { name: "Accept" }));
    await waitFor(() => expect(localStorage.length).toBe(1));
    const saved = JSON.parse(localStorage.getItem(localStorage.key(0) as string) as string);
    expect(saved.decisions[0]).toMatchObject({ id: "post-0", humanTags: ["OPTIONS", "GAMMA", "VOL"] });
    expect(JSON.stringify(saved)).not.toContain("Synthetic source material");
    expect(JSON.stringify(saved)).not.toContain("images.example.invalid");
    expect(JSON.stringify(saved)).not.toContain("aliasToArm");
    expect(JSON.stringify(saved)).not.toContain('"A"');
  });

  it("rejects packets without the required 40 image posts", async () => {
    render(<SlmReview reviewer="operator-fixture" />);
    const invalid = packet();
    invalid.items = invalid.items.map((item) => ({ ...item, imageUrls: [] }));
    const file = new File([JSON.stringify(invalid)], "review-packet.json", { type: "application/json" });
    fireEvent.change(screen.getByLabelText("Load review packet", { selector: 'input[type="file"]' }), { target: { files: [file] } });
    expect(await screen.findByRole("alert")).toHaveTextContent("at least 40 image posts");
  });
});
