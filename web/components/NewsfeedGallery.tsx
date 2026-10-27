"use client";

import { useEffect, useRef, useState } from "react";
import Image from "next/image";
import { ChevronLeft, ChevronRight, Expand, Shrink, ZoomIn, ZoomOut } from "lucide-react";
import type { NewsfeedLightboxFocus } from "./NewsfeedLightbox";
import { getImageSource } from "@/lib/newsfeedSource";

type Props = {
  post: NewsfeedLightboxFocus["post"];
  imageUrl: string;
  onSelect: (url: string) => void;
  expanded: boolean;
  onToggleExpanded: () => void;
};

/** Image navigation is local to this region; article navigation stays in the reader toolbar. */
export default function NewsfeedGallery({ post, imageUrl, onSelect, expanded, onToggleExpanded }: Props) {
  const images = Array.from(new Set([
    ...(post.source?.figures.map(figure => figure.url) ?? []),
    ...(post.images ?? []), imageUrl,
  ].filter(Boolean)));
  const index = images.indexOf(imageUrl);
  const figure = post.source?.figures.find(item => item.url === imageUrl);
  const source = getImageSource(post, imageUrl);
  const [zoomed, setZoomed] = useState(false);
  const [failedUrl, setFailedUrl] = useState<string | null>(null);
  const stageRef = useRef<HTMLDivElement>(null);
  const selectedRef = useRef<HTMLButtonElement>(null);
  const touch = useRef<{ x: number; y: number } | null>(null);
  const enlarged = expanded && zoomed;

  useEffect(() => {
    selectedRef.current?.scrollIntoView?.({ block: "nearest", inline: "nearest" });
  }, [imageUrl]);

  function select(next: number) {
    if (next < 0 || next >= images.length) return;
    setZoomed(false);
    onSelect(images[next]);
    stageRef.current?.scrollTo?.(0, 0);
  }

  return (
    <section className="newsfeed-lightbox__media" role="region" aria-label="Article images" aria-roledescription="carousel" tabIndex={0}
      onKeyDown={event => {
        if (event.altKey || event.metaKey || event.ctrlKey || enlarged) return;
        const next = event.key === "ArrowLeft" ? index - 1 : event.key === "ArrowRight" ? index + 1 : event.key === "Home" ? 0 : event.key === "End" ? images.length - 1 : null;
        if (next == null) return;
        event.preventDefault(); event.stopPropagation(); select(next);
      }}>
      <div className="newsfeed-lightbox__gallery-toolbar">
        <span role="status" aria-live="polite" aria-atomic="true">Image {index + 1} of {images.length}</span>
        <div className="newsfeed-lightbox__controls">
          {expanded ? <button key="zoom" type="button" className="newsfeed-lightbox__control" aria-label={enlarged ? "Fit image" : "Zoom in"}
            onClick={() => setZoomed(!zoomed)}>{enlarged ? <ZoomOut size={18} /> : <ZoomIn size={18} />}<span>{enlarged ? "Fit" : "Zoom"}</span></button> : null}
          <button key="expand" type="button" className="newsfeed-lightbox__control" aria-label={expanded ? "Back to article" : "Enlarge image"}
            onClick={() => { setZoomed(false); onToggleExpanded(); }}>
            {expanded ? <Shrink size={18} /> : <Expand size={18} />}<span>{expanded ? "Back to article" : "Enlarge"}</span>
          </button>
        </div>
      </div>
      <div ref={stageRef} className={`newsfeed-lightbox__stage${enlarged ? " newsfeed-lightbox__stage--zoomed" : ""}`}
        tabIndex={enlarged ? 0 : undefined} aria-label={enlarged ? "Zoomed image, scroll to inspect" : undefined}
        onTouchStart={event => {
          touch.current = event.touches.length === 1 && !enlarged ? { x: event.touches[0].clientX, y: event.touches[0].clientY } : null;
        }}
        onTouchCancel={() => { touch.current = null; }}
        onTouchEnd={event => {
          const start = touch.current; touch.current = null;
          if (!start || !event.changedTouches[0] || enlarged) return;
          const dx = event.changedTouches[0].clientX - start.x;
          const dy = event.changedTouches[0].clientY - start.y;
          if (Math.abs(dx) >= 48 && Math.abs(dx) > Math.abs(dy) * 1.25) select(index + (dx < 0 ? 1 : -1));
        }}>
        {failedUrl === imageUrl ? <p className="newsfeed-lightbox__image-unavailable">Image unavailable. <a href={imageUrl} target="_blank" rel="noopener noreferrer">Open original image</a></p> :
          <Image key={imageUrl} src={imageUrl} alt={figure?.caption || `${post.title}, image ${index + 1}`}
            width={1600} height={900} sizes={expanded ? "100vw" : "(max-width: 900px) 100vw, 55vw"}
            className="newsfeed-lightbox__image" unoptimized={Boolean(post.source)} loading="eager"
            onError={() => setFailedUrl(imageUrl)} />}
      </div>
      <div className="newsfeed-lightbox__gallery-footer">
        <div className="newsfeed-lightbox__gallery-caption-row">
          <p className="newsfeed-lightbox__caption" tabIndex={figure || source ? 0 : undefined}>{figure ? <>{post.source?.publisher}{"page" in figure ? ` · p. ${figure.page}` : ""} · {figure.caption}</> : source ? `Source: ${source}` : ""}</p>
          {images.length > 1 ? <div className="newsfeed-lightbox__controls">
            <button type="button" className="newsfeed-lightbox__control" aria-label="Previous image" aria-disabled={index === 0} onClick={() => select(index - 1)}><ChevronLeft size={20} /></button>
            <button type="button" className="newsfeed-lightbox__control" aria-label="Next image" aria-disabled={index === images.length - 1} onClick={() => select(index + 1)}><ChevronRight size={20} /></button>
          </div> : null}
        </div>
        {images.length > 1 ? <div className="newsfeed-lightbox__thumbnails" aria-label="Choose an image">
          {images.map((url, i) => {
            const item = post.source?.figures.find(item => item.url === url);
            return <button key={url} type="button" ref={url === imageUrl ? selectedRef : undefined}
              aria-label={item ? `View chart ${i + 1}: ${item.caption}` : `View image ${i + 1}`}
              aria-pressed={url === imageUrl} onClick={() => select(i)}>
              <Image src={url} alt="" width={100} height={64} unoptimized={Boolean(post.source)} />
              <span>{item && "page" in item ? `${i + 1} · p. ${item.page}` : i + 1}</span>
            </button>;
          })}
        </div> : null}
      </div>
    </section>
  );
}
