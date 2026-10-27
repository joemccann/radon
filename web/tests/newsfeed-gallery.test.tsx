/** @vitest-environment jsdom */
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import NewsfeedLightbox from "@/components/NewsfeedLightbox";
vi.mock("next/image", () => ({ default: ({ unoptimized: _unoptimized, ...props }: React.ImgHTMLAttributes<HTMLImageElement> & { unoptimized?: boolean }) => <img {...props} /> }));
const toggleBookmark = vi.hoisted(() => vi.fn());
vi.mock("@/lib/useBookmarks", () => ({ useBookmarks: () => ({ isBookmarked: () => false, toggleBookmark }) }));
vi.mock("@/components/NewsfeedExplain", () => ({ default: () => <textarea aria-label="Explanation notes" /> }));
vi.mock("@/components/NewsfeedShare", () => ({ default: () => <button>Share</button> }));
const post = { id: "gallery", title: "Research article", content: "Read the source evidence.", timestamp: "2026-10-27T12:00:00Z", isoTimestamp: "2026-10-27T12:00:00Z", href: "https://example.com/report", images: ["/first.png", "/second.png"], imageSources: { "/first.png": "LSEG", "/second.png": "JPMorgan" } };
const focus = { post, imageUrl: "/first.png" };
afterEach(() => { cleanup(); vi.clearAllMocks(); });
describe("article gallery", () => {
 it("has an explicit close control", () => {
  const dismiss = vi.fn(); render(<NewsfeedLightbox focus={focus} onDismiss={dismiss} />);
  fireEvent.click(screen.getByRole("button", { name: "Close article" })); expect(dismiss).toHaveBeenCalledOnce();
 });
 it("navigates all article images, updates attribution and never changes article at gallery boundaries", () => {
  const navigate = vi.fn(); render(<NewsfeedLightbox focus={focus} onDismiss={vi.fn()} onNavigate={navigate} canNavigateNext />);
  const gallery = screen.getByRole("region", { name: "Article images" });
  fireEvent.keyDown(gallery, { key: "ArrowRight" });
  expect(screen.getByText("Source: JPMorgan")).toBeTruthy();
  expect(screen.getByRole("status").textContent).toBe("Image 2 of 2");
  fireEvent.keyDown(gallery, { key: "ArrowRight" }); expect(navigate).not.toHaveBeenCalled();
  fireEvent.keyDown(gallery, { key: "Home" }); expect(screen.getByText("Source: LSEG")).toBeTruthy();
 });
 it("keeps text editing arrow keys local", () => {
  const navigate = vi.fn(); render(<NewsfeedLightbox focus={focus} onDismiss={vi.fn()} onNavigate={navigate} canNavigateNext />);
  fireEvent.keyDown(screen.getByRole("textbox"), { key: "ArrowRight" }); expect(navigate).not.toHaveBeenCalled();
 });
 it("expands an image and Escape returns to the article before dismissing", () => {
  const dismiss = vi.fn(); render(<NewsfeedLightbox focus={focus} onDismiss={dismiss} />);
  fireEvent.click(screen.getByRole("button", { name: "Enlarge image" }));
  expect(screen.getByRole("button", { name: "Back to article" })).toBeTruthy();
  fireEvent.keyDown(document, { key: "Escape" }); expect(dismiss).not.toHaveBeenCalled();
  expect(screen.getByRole("button", { name: "Enlarge image" })).toBeTruthy();
  fireEvent.keyDown(document, { key: "Escape" }); expect(dismiss).toHaveBeenCalledOnce();
 });
 it("resets gallery when another article opens", () => {
  const { rerender } = render(<NewsfeedLightbox focus={focus} onDismiss={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: "Next image" }));
  rerender(<NewsfeedLightbox focus={{ post: { ...post, id: "next" }, imageUrl: "/first.png" }} onDismiss={vi.fn()} />);
  expect(screen.getByText("Source: LSEG")).toBeTruthy();
 });
});

it("supports swiping within the gallery and ignores vertical or multi-touch gestures", () => {
 const navigate=vi.fn(); render(<NewsfeedLightbox focus={focus} onDismiss={vi.fn()} onNavigate={navigate} canNavigateNext />);
 const stage=document.querySelector(".newsfeed-lightbox__stage")!;
 fireEvent.touchStart(stage,{touches:[{clientX:200,clientY:100}]});
 fireEvent.touchEnd(stage,{changedTouches:[{clientX:100,clientY:110}]});
 expect(screen.getByRole("status").textContent).toBe("Image 2 of 2");
 fireEvent.touchStart(stage,{touches:[{clientX:100,clientY:100}]});
 fireEvent.touchEnd(stage,{changedTouches:[{clientX:200,clientY:300}]});
 expect(screen.getByRole("status").textContent).toBe("Image 2 of 2");
 fireEvent.touchStart(stage,{touches:[{clientX:100,clientY:100},{clientX:200,clientY:100}]});
 fireEvent.touchEnd(stage,{changedTouches:[{clientX:200,clientY:110}]});
 expect(screen.getByRole("status").textContent).toBe("Image 2 of 2");
 expect(navigate).not.toHaveBeenCalled();
});
it("keeps failed image recovery and article content available", () => {
 render(<NewsfeedLightbox focus={focus} onDismiss={vi.fn()} />);
 fireEvent.error(document.querySelector(".newsfeed-lightbox__image")!);
 expect(screen.getByRole("link",{name:"Open original image"}).getAttribute("href")).toBe("/first.png");
 fireEvent.click(screen.getByRole("button",{name:"Next image"}));
 expect(document.querySelector(".newsfeed-lightbox__image")?.getAttribute("src")).toBe("/second.png");
 expect(screen.getByText(post.content)).toBeTruthy();
});
it("keeps zoom scroll keys within the image and fits the next image", () => {
 const navigate=vi.fn(); render(<NewsfeedLightbox focus={focus} onDismiss={vi.fn()} onNavigate={navigate} canNavigateNext />);
 fireEvent.click(screen.getByRole("button",{name:"Enlarge image"}));
 fireEvent.click(screen.getByRole("button",{name:"Zoom in"}));
 const gallery=screen.getByRole("region",{name:"Article images"});
 fireEvent.keyDown(gallery,{key:"ArrowRight"});
 expect(screen.getByRole("status").textContent).toBe("Image 1 of 2");
 expect(navigate).not.toHaveBeenCalled();
 fireEvent.click(screen.getByRole("button",{name:"Next image"}));
 expect(screen.getByRole("button",{name:"Zoom in"})).toBeTruthy();
 expect(screen.getByRole("status").textContent).toBe("Image 2 of 2");
});
it("supports image endpoints, modifiers, and the explicit return control", () => {
 render(<NewsfeedLightbox focus={focus} onDismiss={vi.fn()} />);
 const gallery=screen.getByRole("region",{name:"Article images"});
 fireEvent.keyDown(gallery,{key:"End",ctrlKey:true});
 expect(screen.getByRole("status").textContent).toBe("Image 1 of 2");
 fireEvent.click(screen.getByRole("button",{name:"Previous image"}));
 expect(screen.getByRole("status").textContent).toBe("Image 1 of 2");
 fireEvent.keyDown(gallery,{key:"End"});
 expect(screen.getByRole("status").textContent).toBe("Image 2 of 2");
 fireEvent.click(screen.getByRole("button",{name:"Enlarge image"}));
 fireEvent.click(screen.getByRole("button",{name:"Zoom in"}));
 fireEvent.click(screen.getByRole("button",{name:"Fit image"}));
 fireEvent.click(screen.getByRole("button",{name:"Back to article"}));
 expect(screen.getByRole("button",{name:"Enlarge image"})).toBeTruthy();
 fireEvent.click(screen.getByRole("button",{name:"Previous image"}));
 expect(screen.getByRole("status").textContent).toBe("Image 1 of 2");
});

it("saves the article snapshot and prevents duplicate saves while pending", async () => {
 let finish!: () => void;
 toggleBookmark.mockImplementationOnce(() => new Promise<void>(resolve => { finish = resolve; }));
 render(<NewsfeedLightbox focus={focus} onDismiss={vi.fn()} />);
 const save = screen.getByRole("button", { name: /Save/ });
 fireEvent.click(save);
 expect(save.hasAttribute("disabled")).toBe(true);
 expect(toggleBookmark).toHaveBeenCalledWith({
  id: post.id,
  snapshot: { title: post.title, source: post.href, timestamp: post.isoTimestamp, image: "/first.png", thumbnail: "/first.png" },
 });
 await act(async () => finish());
 await waitFor(() => expect(save.hasAttribute("disabled")).toBe(false));
});

it("matches visible article labels for voice control and navigates in both directions", () => {
 const navigate = vi.fn();
 render(<NewsfeedLightbox focus={focus} onDismiss={vi.fn()} onNavigate={navigate} canNavigatePrev canNavigateNext />);
 fireEvent.click(screen.getByRole("button", { name: "Previous article", exact: true }));
 fireEvent.click(screen.getByRole("button", { name: "Next article", exact: true }));
 expect(navigate.mock.calls).toEqual([[-1], [1]]);
});
