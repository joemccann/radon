"use client";

import { useEffect, useId, useMemo, useRef, useState } from "react";
import { Share2 } from "lucide-react";
import { buildShareCaption, buildXShareUrl, sanitizeShareText, renderShareCard, canvasToPng, canvasToMp4, supportsMp4Export, type SharePost } from "@/lib/newsfeedShare";
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
  const [caption, setCaption] = useState(() => buildShareCaption(post, imageUrl));
  const original = useRef(post);
  const originalImage = useRef(imageUrl);
  const [rewrite, setRewrite] = useState<{ title: string; content: string }>();
  const [rewriting, setRewriting] = useState(true);
  const [voiceError, setVoiceError] = useState("");
  const [voiceAttempt, setVoiceAttempt] = useState(0);
  const sharePost = useMemo(() => rewrite ? { ...post, ...rewrite } : post, [post, rewrite]);
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
    const abort = new AbortController();
    let cancelled = false;
    setRewriting(true);
    setVoiceError("");
    // Defer past StrictMode's setup/cleanup probe to avoid duplicate paid requests.
    const start = setTimeout(() => { void fetch("/api/newsfeed/share", {
      method: "POST", cache: "no-store", signal: AbortSignal.any([abort.signal, AbortSignal.timeout(30_000)]),
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: sanitizeShareText(original.current.title), content: buildShareCaption({ ...original.current, title: "" }, originalImage.current) }),
    }).then(async response => {
      if (!response.ok) throw new Error("Voice rewrite unavailable. Showing the original copy.");
      const draft: unknown = await response.json();
      if (!draft || typeof draft !== "object" || !("title" in draft) || !("content" in draft)
        || typeof draft.title !== "string" || typeof draft.content !== "string"
        || !sanitizeShareText(draft.title) || !sanitizeShareText(draft.content)) {
        throw new Error("Voice rewrite unavailable. Showing the original copy.");
      }
      if (cancelled) return;
      const next = { title: sanitizeShareText(draft.title), content: sanitizeShareText(draft.content) };
      setRewrite(next);
      setCaption(buildShareCaption({ ...original.current, ...next }, originalImage.current));
    }).catch(() => {
      if (!cancelled) setVoiceError("Voice rewrite unavailable. Showing the original copy.");
    }).finally(() => { if (!cancelled) setRewriting(false); }); }, 0);
    return () => { cancelled = true; clearTimeout(start); abort.abort(); };
  }, [voiceAttempt]);

  useEffect(() => {
    let cancelled = false;
    let url: string | undefined;
    canvas.current = null;
    setPreview(undefined);
    controller.current?.abort();
    setError("");
    if (rewriting) return;
    void renderShareCard(sharePost, imageUrl).then(async rendered => {
      const blob = await canvasToPng(rendered);
      if (cancelled) return;
      canvas.current = rendered;
      url = URL.createObjectURL(blob);
      setPreview(url);
    }).catch(err => { if (!cancelled) setError(err instanceof Error ? err.message : "Could not prepare the image. Retry to export."); });
    return () => { cancelled = true; if (url) URL.revokeObjectURL(url); };
  }, [sharePost, imageUrl, attempt, rewriting]);

  async function download(video: boolean) {
    if (!canvas.current || busy || rewriting) return;
    setBusy(true); setError(""); setMessage("");
    controller.current = new AbortController();
    try {
      const blob = video ? await canvasToMp4(canvas.current, controller.current.signal) : await canvasToPng(canvas.current);
      if (!active.current) return;
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `radon-${sanitizeShareText(post.title).toLowerCase().replace(/[^a-z0-9]+/g, "-").slice(0,60)}.${video ? "mp4" : "png"}`;
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
    try { await navigator.clipboard.writeText(sanitizeShareText(caption)); setMessage("Caption copied."); }
    catch { setError("Copy unavailable. Select and copy the caption below."); }
  }

  return <section id={panelId} className={styles.panel} aria-label="Share news item" aria-busy={busy || rewriting}>
    <div className={styles.heading}><strong>Share this analysis</strong><span>1080 × 1920</span></div>
    <div className={styles.layout}>
      <div className={styles.preview}>
        {/* Local blob generated on demand; next/image cannot optimize it. */}
        {/* eslint-disable-next-line @next/next/no-img-element */}
        {preview ? <img src={preview} alt={`Portrait share preview: ${sanitizeShareText(post.title)}`} /> : <span>{error ? "Preview unavailable" : "Preparing preview…"}</span>}
      </div>
      <div className={styles.actions}>
        <a className={styles.action} href={buildXShareUrl(caption)} target="_blank" rel="noopener noreferrer">Compose on X</a>
        <button type="button" disabled={!preview || busy || rewriting} onClick={() => void download(false)}>Download Story image</button>
        <button type="button" disabled={!preview || busy || rewriting || !mp4} onClick={() => void download(true)}>{busy ? "Exporting…" : "Download Reels / TikTok video"}</button>
        <p>Save the image or video, then upload in your social app. Attach images separately on X.</p>
        {!mp4 ? <p>MP4 export is unavailable in this browser. Use a browser with MP4 recording support, or import the image in your video editor.</p> : null}
      </div>
    </div>
    <label className={styles.label} htmlFor={id}>Post caption</label>
    <textarea id={id} disabled={rewriting} value={caption} onChange={event => setCaption(event.target.value)} rows={4} />
    <div className={styles.captionActions}><button type="button" disabled={rewriting} onClick={() => void copyCaption()}>Copy caption</button><span>{Array.from(caption).length} characters · edit to fit X</span></div>
    <p className={styles.note}>The image and video use the preview copy. Caption edits apply only to your post.</p>
    {voiceError ? <div role="alert" className={styles.error}>{voiceError} <button type="button" disabled={busy || rewriting} onClick={() => setVoiceAttempt(value => value + 1)}>Retry voice rewrite</button></div> : null}
    {error ? <div role="alert" className={styles.error}>{error} <button type="button" disabled={busy} onClick={() => { setError(""); setAttempt(value => value + 1); }}>Retry</button></div> : null}
    <p role="status" className={styles.status}>{rewriting ? "Writing in your voice…" : message || (busy ? "Creating your video. Keep this panel open." : "")}</p>
  </section>;
}
