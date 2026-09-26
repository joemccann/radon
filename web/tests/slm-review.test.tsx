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

describe("private SLM blind review", () => {
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

  it("keeps candidates hidden until human labels are locked, shows images, and persists only decisions", async () => {
    render(<SlmReview reviewer="operator-fixture" />);
    const file = new File([JSON.stringify(packet())], "review-packet.json", { type: "application/json" });
    fireEvent.change(screen.getByLabelText("Load review packet", { selector: 'input[type="file"]' }), { target: { files: [file] } });
    await screen.findByText("Synthetic held-out title");
    expect(screen.getByRole("status").textContent).toContain("fixture-run");
    expect(screen.queryByText("Candidate 1")).toBeNull();
    expect(screen.getByAltText("Post image 1 from images.example.invalid")).toBeTruthy();
    expect(screen.getByAltText("Post image 2 from second.example.invalid")).toBeTruthy();

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

  it("restores packet, exact position, draft labels, and previous votes after a restart", async () => {
    render(<SlmReview reviewer="operator-fixture" />);
    fireEvent.change(screen.getByLabelText("Load review packet", { selector: 'input[type="file"]' }), {
      target: { files: [new File([JSON.stringify(packet())], "packet.json", { type: "application/json" })] },
    });
    await screen.findByText("Synthetic held-out title");
    fireEvent.change(screen.getByLabelText("TAG 1"), { target: { value: "OPTIONS" } });
    fireEvent.change(screen.getByLabelText("TAG 2"), { target: { value: "GAMMA" } });
    fireEvent.change(screen.getByLabelText("TAG 3"), { target: { value: "VOL" } });
    fireEvent.click(screen.getByRole("button", { name: "Lock labels and compare" }));
    const candidate = screen.getByText("Candidate 1").parentElement as HTMLElement;
    fireEvent.click(within(candidate).getByRole("button", { name: "Accept" }));
    fireEvent.click(screen.getByRole("button", { name: "Skip / next" }));
    fireEvent.change(screen.getByLabelText("TAG 1"), { target: { value: "VOL" } });
    await waitFor(() => expect(JSON.parse(localStorage.getItem("radon-slm-review:fixture-run:operator-fixture") as string).index).toBe(1));
    cleanup();

    render(<SlmReview reviewer="operator-fixture" />);
    await screen.findByText("POST post-1");
    expect((screen.getByLabelText("TAG 1") as HTMLInputElement).value).toBe("VOL");
    fireEvent.click(screen.getByRole("button", { name: "Previous" }));
    expect(within(screen.getByText("Candidate 1").parentElement as HTMLElement).getByRole("button", { name: "Accept" }).getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByRole("button", { name: "Export decisions" })).toBeTruthy();
  });

  it("restores backed-up decisions when this browser has no saved votes", async () => {
    browserPacket.value = packet();
    vi.stubGlobal("fetch", vi.fn(async (_url: string, options?: RequestInit) => ({
      ok: true,
      json: async () => options?.method === "PUT" ? JSON.parse(options.body as string) : {
        schema: "radon.slm-review-decisions.v1", runId: "fixture-run", reviewer: "operator-fixture", index: 8,
        decisions: [{ id: "post-8", humanTags: ["OPTIONS", "GAMMA", "VOL"], acceptance: { "Candidate 1": true }, reviewer: "operator-fixture", reviewedAt: "2026-09-25T12:00:00.000Z" }],
      },
    })));
    render(<SlmReview reviewer="operator-fixture" />);
    await screen.findByText("POST post-8");
    expect(within(screen.getByText("Candidate 1").parentElement as HTMLElement).getByRole("button", { name: "Accept" }).getAttribute("aria-pressed")).toBe("true");
    expect(JSON.parse(localStorage.getItem("radon-slm-review:fixture-run:operator-fixture") as string).decisions).toHaveLength(1);
  });

  it("opens the first unfinished item when importing legacy browser votes without a cursor", async () => {
    browserPacket.value = packet();
    localStorage.setItem("radon-slm-review:fixture-run:operator-fixture", JSON.stringify({
      schema: "radon.slm-review-decisions.v1", runId: "fixture-run", reviewer: "operator-fixture",
      decisions: [{
        id: "post-0", humanTags: ["OPTIONS", "GAMMA", "VOL"],
        acceptance: { "Candidate 1": true, "Candidate 2": false, "Candidate 3": true },
        reviewer: "operator-fixture", reviewedAt: "2026-09-25T12:00:00.000Z",
      }],
    }));
    render(<SlmReview reviewer="operator-fixture" />);
    await screen.findByText("POST post-1");
    expect(screen.getByText(/Resumed 1 labeled item/)).toBeTruthy();
  });

  it("rejects packets without the required 40 image posts", async () => {
    render(<SlmReview reviewer="operator-fixture" />);
    const invalid = packet();
    invalid.items = invalid.items.map((item) => ({ ...item, imageUrls: [] }));
    const file = new File([JSON.stringify(invalid)], "review-packet.json", { type: "application/json" });
    fireEvent.change(screen.getByLabelText("Load review packet", { selector: 'input[type="file"]' }), { target: { files: [file] } });
    expect((await screen.findByRole("alert")).textContent).toContain("at least 40 image posts");
  });
});
