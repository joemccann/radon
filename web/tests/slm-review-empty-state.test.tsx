// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import SlmReview from "../app/admin/slm-review/slm-review";

const browserPacket = vi.hoisted(() => ({ value: null as unknown }));
vi.mock("../app/admin/slm-review/packetStorage", () => ({
  loadReviewPacket: async () => browserPacket.value,
  saveReviewPacket: async (_reviewer: string, packet: unknown) => { browserPacket.value = packet; },
}));

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

describe("slm-review empty state (plate 22)", () => {
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
  beforeEach(() => {
    localStorage.clear();
    browserPacket.value = null;
    vi.stubGlobal("fetch", vi.fn(async (_url: string, options?: RequestInit) => ({
      ok: true,
      json: async () => options?.method === "PUT" ? JSON.parse(options.body as string) : {
        schema: "radon.slm-review-decisions.v1", runId: "fixture-run", reviewer: "operator-fixture", decisions: [], index: 0,
      },
    })));
  });

  it("offers exactly one load action before a packet is loaded", async () => {
    render(<SlmReview reviewer="operator-fixture" />);
    await screen.findByTestId("slm-review-dropzone");
    expect(screen.queryByRole("button", { name: "Load review packet" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Choose JSON file" })).toBeNull();
    expect(screen.getAllByRole("button", { name: "choose file" })).toHaveLength(1);
    expect(screen.getByTestId("slm-review-dropzone").textContent).toContain("200 posts · at least 40 with images · 3 aligned prediction sets");
  });

  it("loads a packet dropped onto the empty state", async () => {
    render(<SlmReview reviewer="operator-fixture" />);
    const zone = await screen.findByTestId("slm-review-dropzone");
    const file = new File([JSON.stringify(packet())], "review-packet.json", { type: "application/json" });
    fireEvent.dragOver(zone, { dataTransfer: { files: [file] } });
    fireEvent.drop(zone, { dataTransfer: { files: [file] } });
    await screen.findByText("Synthetic held-out title");
    await waitFor(() => expect(screen.getByRole("button", { name: "Load review packet" })).toBeTruthy());
  });
});
