"use client";

import { useEffect, useId, useRef, useState } from "react";
import { Share2 } from "lucide-react";
import { buildShareCaption, renderShareCard, canvasToPng, canvasToMp4, supportsMp4Export, type SharePost } from "@/lib/newsfeedShare";
import styles from "./NewsfeedShare.module.css";

export default function NewsfeedShare({ post, imageUrl }: { post: SharePost; imageUrl?: string }) {
  const [open, setOpen] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  const panelId = useId();
  return <div className={styles.root} data-newsfeed-share onKeyDown={event => {
    if (event.key === "Escape" && open) { event.stopPropagation(); setOpen(false); trigger.current?.focus(); }
    if (event.key === "ArrowLeft" || event.key === "ArrowRight") event.stopPropagation();
  }} onTouchStart={event => event.stopPropagation()} onTouchEnd={event => event.stopPropagation()}>
    <button type="button" className={styles.trigger} ref={trigger} aria-expanded={open} aria-controls={open ? panelId : undefined}
      onClick={() => setOpen(value => !value)}><Share2 size={15} aria-hidden /> Share</button>
    {open ? <SharePanel panelId={panelId} key={post.id} post={post} imageUrl={imageUrl} /> : null}
  </div>;
}

function SharePanel({ post, imageUrl, panelId }: { post: SharePost; imageUrl?: string; panelId: string }) {
  const id = useId();
  const [caption, setCaption] = useState(() => buildShareCaption(post));
  const [preview, setPreview] = useState<string>();
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [mp4] = useState(supportsMp4Export);
  const [attempt, setAttempt] = useState(0);
  const canvas = useRef<HTMLCanvasElement | null>(null);
  const controller = useRef<AbortController | null>(null);
  const active = useRef(true);

  useEffect(() => {
    active.current = true;
    return () => { active.current = false; controller.current?.abort(); };
  }, []);

  useEffect(() => {
    let cancelled = false;
    let url: string | undefined;
    canvas.current = null;
    setPreview(undefined);
    controller.current?.abort();
    setError("");
    void renderShareCard(post, imageUrl).then(async rendered => {
      const blob = await canvasToPng(rendered);
      if (cancelled) return;
      canvas.current = rendered;
      url = URL.createObjectURL(blob);
      setPreview(url);
    }).catch(err => { if (!cancelled) setError(err instanceof Error ? err.message : "Could not prepare the image. Retry to export."); });
    return () => { cancelled = true; if (url) URL.revokeObjectURL(url); };
  }, [post, imageUrl, attempt]);

  async function download(video: boolean) {
    if (!canvas.current || busy) return;
    setBusy(true); setError(""); setMessage("");
    controller.current = new AbortController();
    try {
      const blob = video ? await canvasToMp4(canvas.current, controller.current.signal) : await canvasToPng(canvas.current);
      if (!active.current) return;
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `radon-${post.title.toLowerCase().replace(/[^a-z0-9]+/g, "-").slice(0,60)}.${video ? "mp4" : "png"}`;
      anchor.click();
      setTimeout(() => URL.revokeObjectURL(url), 30_000);
      setMessage(video ? "Video downloaded. Upload it in Instagram or TikTok and paste your caption." : "Image downloaded. Add it to your Story or attach it to your X post.");
    } catch (err) {
      if (active.current && !(err instanceof DOMException && err.name === "AbortError")) {
        setError(err instanceof Error ? err.message : "Export failed. Try again.");
      }
    } finally { if (active.current) setBusy(false); }
  }

  async function copyCaption() {
    try { await navigator.clipboard.writeText(caption); setMessage("Caption copied."); }
    catch { setError("Copy unavailable. Select and copy the caption below."); }
  }

  return <section id={panelId} className={styles.panel} aria-label="Share news item" aria-busy={busy}>
    <div className={styles.heading}><strong>Share this analysis</strong><span>1080 × 1920</span></div>
    <div className={styles.layout}>
      <div className={styles.preview}>
        {/* Local blob generated on demand; next/image cannot optimize it. */}
        {/* eslint-disable-next-line @next/next/no-img-element */}
        {preview ? <img src={preview} alt={`Portrait share preview: ${post.title}`} /> : <span>{error ? "Preview unavailable" : "Preparing preview…"}</span>}
      </div>
      <div className={styles.actions}>
        <a className={styles.action} href={`https://twitter.com/intent/tweet?${new URLSearchParams({text:caption})}`} target="_blank" rel="noopener noreferrer">Compose on X</a>
        <button type="button" disabled={!preview || busy} onClick={() => void download(false)}>Download Story image</button>
        <button type="button" disabled={!preview || busy || !mp4} onClick={() => void download(true)}>{busy ? "Exporting…" : "Download Reels / TikTok video"}</button>
        <p>Save the image or video, then upload in your social app. Attach images separately on X.</p>
        {!mp4 ? <p>MP4 export is unavailable in this browser. Use a browser with MP4 recording support, or import the image in your video editor.</p> : null}
      </div>
    </div>
    <label className={styles.label} htmlFor={id}>Post caption</label>
    <textarea id={id} value={caption} onChange={event => setCaption(event.target.value)} rows={4} />
    <div className={styles.captionActions}><button type="button" onClick={() => void copyCaption()}>Copy caption</button><span>{Array.from(caption).length} characters · edit to fit X</span></div>
    <p className={styles.note}>Caption edits apply to your post. The exported card keeps the original analysis and source attribution.</p>
    {error ? <div role="alert" className={styles.error}>{error} <button type="button" disabled={busy} onClick={() => { setError(""); setAttempt(value => value + 1); }}>Retry</button></div> : null}
    <p role="status" className={styles.status}>{message || (busy ? "Creating your video. Keep this panel open." : "")}</p>
  </section>;
}
