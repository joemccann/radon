/** @vitest-environment jsdom */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ResearchFeedback from "../components/ResearchFeedback";

const POST_ID = "research-" + "a".repeat(64);
const fetchMock = vi.fn();

beforeEach(() => {
  fetchMock.mockReset();
  fetchMock.mockResolvedValue(new Response(JSON.stringify({ id: "v1", postId: POST_ID, vote: "down", hidden: true }), { status: 200 }));
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

function sent() {
  const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
  return { url, method: init.method, body: JSON.parse(String(init.body)) };
}

describe("ResearchFeedback", () => {
  it("opening the panel and picking chips sends nothing; Save sends the exact vote", async () => {
    const onHidden = vi.fn();
    render(<ResearchFeedback postId={POST_ID} onHidden={onHidden} />);
    fireEvent.click(screen.getByRole("button", { name: "Thumbs down" }));
    fireEvent.click(screen.getByRole("button", { name: "Not relevant to me" }));
    fireEvent.change(screen.getByLabelText("Comment (optional)"), { target: { value: "single stock, not for me" } });
    expect(fetchMock).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Save feedback" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(sent()).toEqual({
      url: "/api/newsfeed/research/feedback", method: "POST",
      body: { postId: POST_ID, vote: "down", reasons: ["not_relevant"], comment: "single stock, not for me" },
    });
    await waitFor(() => expect(onHidden).toHaveBeenCalledWith(POST_ID));
  });

  it("a thumbs-up offers Want more and does not hide the post", async () => {
    const onHidden = vi.fn();
    render(<ResearchFeedback postId={POST_ID} onHidden={onHidden} />);
    fireEvent.click(screen.getByRole("button", { name: "Thumbs up" }));
    expect(screen.queryByRole("button", { name: "Not relevant to me" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Want more (chart, detail)" }));
    fireEvent.change(screen.getByLabelText("Comment (optional)"), { target: { value: "add the dot plot chart" } });
    fireEvent.click(screen.getByRole("button", { name: "Save feedback" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(sent().body).toEqual({ postId: POST_ID, vote: "up", reasons: ["want_more"], comment: "add the dot plot chart" });
    await screen.findByText("Saved");
    expect(onHidden).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Thumbs up" }).getAttribute("aria-pressed")).toBe("true");
  });

  it("shows an existing vote as pressed", () => {
    render(<ResearchFeedback postId={POST_ID} initial={{ vote: "up", reasons: ["want_more"], comment: "chart" }} onHidden={vi.fn()} />);
    expect(screen.getByRole("button", { name: "Thumbs up" }).getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByRole("button", { name: "Thumbs down" }).getAttribute("aria-pressed")).toBe("false");
  });

  it("a failed save raises an error toast, keeps the post and can be retried", async () => {
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ error: "Feedback store temporarily unavailable." }), { status: 503 }));
    const onHidden = vi.fn();
    render(<ResearchFeedback postId={POST_ID} onHidden={onHidden} />);
    fireEvent.click(screen.getByRole("button", { name: "Thumbs down" }));
    fireEvent.click(screen.getByRole("button", { name: "Save feedback" }));
    expect((await screen.findByRole("alert")).textContent).toContain("Feedback store temporarily unavailable.");
    expect(onHidden).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(onHidden).toHaveBeenCalledWith(POST_ID));
  });

  it("Save is disabled while a request is in flight", async () => {
    let release: (value: Response) => void = () => {};
    fetchMock.mockReturnValueOnce(new Promise<Response>((resolve) => { release = resolve; }));
    render(<ResearchFeedback postId={POST_ID} onHidden={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Thumbs up" }));
    const save = screen.getByRole("button", { name: "Save feedback" });
    fireEvent.click(save);
    fireEvent.click(save);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    release(new Response(JSON.stringify({ id: "v", postId: POST_ID, vote: "up", hidden: false }), { status: 200 }));
    await screen.findByText("Saved");
  });
});
