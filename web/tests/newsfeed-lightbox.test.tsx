/**
 * @vitest-environment jsdom
 *
 * UI regression guards for NewsfeedLightbox. The lightbox is the only
 * interactive image experience in the dashboard, so a few behavioural
 * pins matter:
 *   - nothing renders when no focus is set
 *   - image + title + body all surface together when focus is set
 *   - Escape and scrim click both fire onDismiss
 *   - brand-token contract holds (no raw hex / rgba())
 *
 * Note: as of the focused-backdrop redesign the lightbox portals to
 * document.body so it can escape the right-rail's stacking context. The
 * RTL `getBy*` helpers query the whole document, so the existing assertions
 * continue to find the portal-rendered content. The only adjustment is the
 * `renders nothing` case: we look at document.body instead of the testing
 * container, since the portal target is body itself.
 */
import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { cleanup, fireEvent, render } from "@testing-library/react";
import NewsfeedLightbox, {
  type NewsfeedLightboxFocus,
} from "@/components/NewsfeedLightbox";

// next/image rejects unconfigured hostnames in jsdom — stub it to a plain
// <img> so the render doesn't crash on the synthetic media.radon.run URL.
vi.mock("next/image", () => ({
  default: ({ src, alt }: { src: string; alt: string }) =>
    React.createElement("img", { src, alt }),
}));

const POST: NewsfeedLightboxFocus["post"] = {
  id: "x1",
  title: "Convexity concentration elevated",
  content: "Dealer gamma exposure flipped overnight; expect pinning to break.",
  timestamp: "2026-05-20T18:30:00Z",
  isoTimestamp: "2026-05-20T18:30:00Z",
  href: "https://themarketear.com/posts/x1",
  images: ["https://media.radon.run/images/x1.png"],
  tags: ["GAMMA", "PINNING"],
};

const FOCUS: NewsfeedLightboxFocus = {
  post: POST,
  imageUrl: "https://media.radon.run/images/x1.png",
};

describe("NewsfeedLightbox", () => {
  beforeEach(() => {
    // jsdom does not implement Image at sufficient fidelity for next/image —
    // patch the necessary surface so render() does not crash.
    document.body.style.overflow = "";
  });

  afterEach(() => {
    cleanup();
    document.body.style.overflow = "";
  });

  it("renders nothing when focus is null", () => {
    const onDismiss = vi.fn();
    render(<NewsfeedLightbox focus={null} onDismiss={onDismiss} />);
    // Lightbox portals to body — query document.body instead of the RTL
    // test container, since the portal target is body itself.
    expect(document.body.querySelector(".newsfeed-lightbox")).toBeNull();
  });

  it("renders title, body, and tags when focus is set", () => {
    const onDismiss = vi.fn();
    const { getByText } = render(
      <NewsfeedLightbox focus={FOCUS} onDismiss={onDismiss} />,
    );
    expect(getByText(POST.title)).not.toBeNull();
    expect(getByText(/Dealer gamma exposure flipped/i)).not.toBeNull();
    expect(getByText("GAMMA")).not.toBeNull();
    expect(getByText("PINNING")).not.toBeNull();
  });

  it("shows the provider for the selected Market Ear image without leaking it to another chart", () => {
    const first = POST.images![0];
    const second = "https://media.radon.run/images/x1-second.png";
    const third = "https://media.radon.run/images/x1-third.png";
    const post = {
      ...POST,
      images: [first, second, third],
      imageSources: { [first]: "Ramp", [second]: "Goldman Sachs" },
    };
    const { rerender, getByText, queryByText } = render(
      <NewsfeedLightbox focus={{ post, imageUrl: first }} onDismiss={vi.fn()} />,
    );
    expect(getByText("Source: Ramp").closest(".newsfeed-lightbox__media")).not.toBeNull();
    expect(queryByText("Source: Goldman Sachs")).toBeNull();

    rerender(<NewsfeedLightbox focus={{ post, imageUrl: second }} onDismiss={vi.fn()} />);
    expect(getByText("Source: Goldman Sachs").closest(".newsfeed-lightbox__media")).not.toBeNull();
    expect(queryByText("Source: Ramp")).toBeNull();

    rerender(<NewsfeedLightbox focus={{ post, imageUrl: third }} onDismiss={vi.fn()} />);
    expect(queryByText(/^Source:/)).toBeNull();
  });

  it("does not invent an image source for a legacy Market Ear post", () => {
    const { queryByText } = render(<NewsfeedLightbox focus={FOCUS} onDismiss={vi.fn()} />);
    expect(queryByText(/^Source:/)).toBeNull();
  });

  it("fires onDismiss when the scrim is clicked", () => {
    const onDismiss = vi.fn();
    const { getByTestId } = render(
      <NewsfeedLightbox focus={FOCUS} onDismiss={onDismiss} />,
    );
    fireEvent.click(getByTestId("newsfeed-lightbox-scrim"));
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });

  it("fires onDismiss on Escape", () => {
    const onDismiss = vi.fn();
    render(<NewsfeedLightbox focus={FOCUS} onDismiss={onDismiss} />);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });

  it("locks body scroll while open and restores on close", () => {
    const onDismiss = vi.fn();
    const { rerender } = render(
      <NewsfeedLightbox focus={FOCUS} onDismiss={onDismiss} />,
    );
    expect(document.body.style.overflow).toBe("hidden");
    rerender(<NewsfeedLightbox focus={null} onDismiss={onDismiss} />);
    expect(document.body.style.overflow).not.toBe("hidden");
  });

  it("ArrowRight fires onNavigate(+1) when canNavigateNext=true", () => {
    const onNavigate = vi.fn();
    render(
      <NewsfeedLightbox
        focus={FOCUS}
        onDismiss={vi.fn()}
        onNavigate={onNavigate}
        canNavigatePrev
        canNavigateNext
      />,
    );
    fireEvent.keyDown(document, { key: "ArrowRight" });
    expect(onNavigate).toHaveBeenCalledTimes(1);
    expect(onNavigate).toHaveBeenCalledWith(1);
  });

  it("ArrowLeft fires onNavigate(-1) when canNavigatePrev=true", () => {
    const onNavigate = vi.fn();
    render(
      <NewsfeedLightbox
        focus={FOCUS}
        onDismiss={vi.fn()}
        onNavigate={onNavigate}
        canNavigatePrev
        canNavigateNext
      />,
    );
    fireEvent.keyDown(document, { key: "ArrowLeft" });
    expect(onNavigate).toHaveBeenCalledTimes(1);
    expect(onNavigate).toHaveBeenCalledWith(-1);
  });

  it("ArrowRight is a no-op when canNavigateNext=false (end of list)", () => {
    const onNavigate = vi.fn();
    render(
      <NewsfeedLightbox
        focus={FOCUS}
        onDismiss={vi.fn()}
        onNavigate={onNavigate}
        canNavigatePrev
        canNavigateNext={false}
      />,
    );
    fireEvent.keyDown(document, { key: "ArrowRight" });
    expect(onNavigate).not.toHaveBeenCalled();
  });

  it("ArrowLeft is a no-op when canNavigatePrev=false (start of list)", () => {
    const onNavigate = vi.fn();
    render(
      <NewsfeedLightbox
        focus={FOCUS}
        onDismiss={vi.fn()}
        onNavigate={onNavigate}
        canNavigatePrev={false}
        canNavigateNext
      />,
    );
    fireEvent.keyDown(document, { key: "ArrowLeft" });
    expect(onNavigate).not.toHaveBeenCalled();
  });

  it("renders prev/next chevrons only when their direction is navigable", () => {
    const { queryByTestId, rerender } = render(
      <NewsfeedLightbox
        focus={FOCUS}
        onDismiss={vi.fn()}
        onNavigate={vi.fn()}
        canNavigatePrev
        canNavigateNext
      />,
    );
    expect(queryByTestId("newsfeed-lightbox-prev")).not.toBeNull();
    expect(queryByTestId("newsfeed-lightbox-next")).not.toBeNull();

    rerender(
      <NewsfeedLightbox
        focus={FOCUS}
        onDismiss={vi.fn()}
        onNavigate={vi.fn()}
        canNavigatePrev={false}
        canNavigateNext={false}
      />,
    );
    expect(queryByTestId("newsfeed-lightbox-prev")).toBeNull();
    expect(queryByTestId("newsfeed-lightbox-next")).toBeNull();
  });

  it("clicking the next chevron fires onNavigate(+1)", () => {
    const onNavigate = vi.fn();
    const { getByTestId } = render(
      <NewsfeedLightbox
        focus={FOCUS}
        onDismiss={vi.fn()}
        onNavigate={onNavigate}
        canNavigatePrev
        canNavigateNext
      />,
    );
    fireEvent.click(getByTestId("newsfeed-lightbox-next"));
    expect(onNavigate).toHaveBeenCalledWith(1);
  });
});

 it("research thumbnails switch the original image and page caption without changing posts", () => {
 const base = "/api/newsfeed/research/files/";
 const first = base + "a".repeat(64) + ".png";
 const second = base + "b".repeat(64) + ".png";
 const source = {kind: "dropbox" as const, publisher: "Synthetic Bank", url: base + "c".repeat(64) + ".pdf", documentDate: "2026-09-07", folderDate: "2026-09-07", pages: [2,3], figures: [{url:first,page:2,caption:"First chart"},{url:second,page:3,caption:"Second chart"}], fileId:"fixture",revision:"r1",contentHash:"c".repeat(64)};
 const {getByRole, getByText} = render(<NewsfeedLightbox focus={{post:{...POST, source, images:[first,second], href:source.url}, imageUrl:first}} onDismiss={vi.fn()} />);
 fireEvent.click(getByRole("button", {name:"View chart 2: Second chart"}));
 expect(document.querySelector(".newsfeed-lightbox__media > img")?.getAttribute("src")).toBe(second);
 expect(getByText("Synthetic Bank · p. 3 · Second chart")).not.toBeNull();
 expect(getByRole("link", {name:"Source PDF"}).getAttribute("href")).toBe(source.url);
 cleanup();
 });
