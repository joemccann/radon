"use client";
import ErrorToast from "@/components/ErrorToast";
import { useEffect, useId, useRef, useState } from "react";
import { CircleHelp } from "lucide-react";
import { EXPLAIN_BUDGET_MS, type ExplainStage } from "@/lib/newsfeedExplainProgress";
import styles from "./NewsfeedExplain.module.css";

const EXPLAIN_CLIENT_TIMEOUT_MS = EXPLAIN_BUDGET_MS + 10_000;
const ERROR_COPY = "Could not explain this post. Try again.";
const ELAPSED_TICK_MS = 250;

type ExplainPost = { id: string; title: string; content: string };
type ExplainResult = { explanation: string; thin?: boolean };

const drafts = new Map<string, ExplainResult>();

type ExplainFrame = { event: string; data: unknown };

function decodeFrame(frame: string): ExplainFrame | null {
  const event = frame.match(/^event: (.+)$/m)?.[1];
  const data = frame.match(/^data: (.+)$/m)?.[1];
  return event && data ? { event, data: JSON.parse(data) } : null;
}

function isStreamed(response: Response): boolean {
  return Boolean(response.headers?.get("content-type")?.includes("text/event-stream"));
}

async function readFrames(
  body: ReadableStream<Uint8Array>,
  onStage: (stage: ExplainStage) => void,
): Promise<unknown> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffered = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) throw new Error(ERROR_COPY);
    buffered += decoder.decode(value, { stream: true });
    const frames = buffered.split("\n\n");
    buffered = frames.pop() ?? "";
    for (const raw of frames) {
      let frame: ExplainFrame | null = null;
      try { frame = decodeFrame(raw); } catch { continue; }
      if (frame?.event === "stage") {
        const stage = (frame.data as { stage?: ExplainStage }).stage;
        if (stage) onStage(stage);
      }
      if (frame?.event === "error") throw new Error(ERROR_COPY);
      if (frame?.event === "result") { void reader.cancel(); return frame.data; }
    }
  }
}

async function readExplain(
  response: Response,
  onStage: (stage: ExplainStage) => void,
): Promise<unknown> {
  if (!isStreamed(response) || !response.body) return response.json();
  return readFrames(response.body, onStage);
}

function parseResult(payload: unknown): ExplainResult {
  if (!payload || typeof payload !== "object") throw new Error(ERROR_COPY);
  const explanation = (payload as { explanation?: unknown }).explanation;
  if (typeof explanation !== "string" || !explanation.trim()) throw new Error(ERROR_COPY);
  return { explanation, thin: (payload as { thin?: unknown }).thin === true };
}

function useElapsedMs(active: boolean): number {
  const [elapsedMs, setElapsedMs] = useState(0);
  useEffect(() => {
    if (!active) { setElapsedMs(0); return; }
    const startedAt = Date.now();
    const tick = setInterval(() => setElapsedMs(Date.now() - startedAt), ELAPSED_TICK_MS);
    return () => clearInterval(tick);
  }, [active]);
  return elapsedMs;
}

export default function NewsfeedExplain({
  post,
  imageUrls,
}: {
  post: ExplainPost;
  imageUrls?: string[];
}) {
  const cached = drafts.get(post.id);
  const trigger = useRef<HTMLButtonElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const panelId = useId();
  const [open, setOpen] = useState(false);
  const [entered, setEntered] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<ExplainResult | undefined>(cached);
  const [stage, setStage] = useState<ExplainStage>("queued");
  const elapsedMs = useElapsedMs(loading);
  const seconds = Math.floor(elapsedMs / 1000);
  const urls = imageUrls ?? [];

  useEffect(() => {
    return () => { abortRef.current?.abort(); };
  }, []);

  useEffect(() => {
    if (!open) { setEntered(false); return; }
    const frame = requestAnimationFrame(() => setEntered(true));
    return () => cancelAnimationFrame(frame);
  }, [open]);

  const state = loading ? "loading"
    : error ? "error"
    : open && result?.thin ? "thin"
    : open && result ? "explained"
    : "idle";

  async function requestExplain() {
    abortRef.current?.abort();
    const abort = new AbortController();
    abortRef.current = abort;
    setError("");
    setOpen(true);
    setLoading(true);
    setStage("queued");
    try {
      const timeout = typeof AbortSignal.timeout === "function"
        ? AbortSignal.timeout(EXPLAIN_CLIENT_TIMEOUT_MS)
        : undefined;
      const signal = timeout && typeof AbortSignal.any === "function"
        ? AbortSignal.any([abort.signal, timeout])
        : abort.signal;
      const response = await fetch("/api/newsfeed/explain", {
        method: "POST",
        cache: "no-store",
        signal,
        headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
        body: JSON.stringify({
          postId: post.id,
          title: post.title,
          content: post.content,
          imageUrls: urls,
        }),
      });
      if (!response.ok) throw new Error(ERROR_COPY);
      const payload = parseResult(await readExplain(response, next => {
        if (abort.signal.aborted) return;
        setStage(next);
      }));
      if (abort.signal.aborted) return;
      drafts.set(post.id, payload);
      setResult(payload);
    } catch (err) {
      if (abort.signal.aborted || (err instanceof DOMException && err.name === "AbortError")) return;
      setError(ERROR_COPY);
    } finally {
      if (!abort.signal.aborted) setLoading(false);
    }
  }

  function onTrigger() {
    if (loading) return;
    if (result && open) { setOpen(false); return; }
    if (result && !open) { setOpen(true); setError(""); return; }
    void requestExplain();
  }

  return <div
    className={styles.root}
    data-testid="newsfeed-explain"
    data-state={state}
    onKeyDown={event => {
      if (event.key === "Escape" && open) {
        event.stopPropagation();
        setOpen(false);
        trigger.current?.focus();
      }
      if (event.key === "ArrowLeft" || event.key === "ArrowRight") event.stopPropagation();
    }}
    onTouchStart={event => event.stopPropagation()}
    onTouchEnd={event => event.stopPropagation()}
  >
    <button
      type="button"
      className={styles.trigger}
      ref={trigger}
      disabled={loading}
      aria-expanded={open}
      aria-controls={open ? panelId : undefined}
      onClick={onTrigger}
    >
      <CircleHelp size={15} strokeWidth={1.5} aria-hidden />
      {loading ? "Explaining" : "Explain"}
    </button>
    {open ? <section
      id={panelId}
      className={entered ? `${styles.panel} ${styles.panelOpen}` : styles.panel}
      aria-label="Explanation"
      aria-busy={loading}
    >
      <h3 className={styles.heading}>Explanation</h3>
      {loading ? <div className={styles.skeleton} aria-hidden>
        <span className={styles.skelLine} />
        <span className={styles.skelLine} />
        <span className={styles.skelLine} />
        <span className={styles.skelLine} />
      </div> : null}
      {loading ? <p className={styles.elapsed} aria-hidden>{seconds}s</p> : null}
      {loading ? <p className={styles.visuallyHidden} role="status">{stage}, {seconds}s elapsed</p> : null}
      {!loading && result ? <p className={styles.body}>{result.explanation}</p> : null}
      <div className={styles.toolbar}>
        {!loading && result ? <button type="button" onClick={() => setOpen(false)}>Hide explanation</button> : null}
        {!loading && error ? <button type="button" onClick={() => void requestExplain()}>Retry</button> : null}
      </div>
    </section> : null}
    {error ? <ErrorToast message={error} onRetry={loading ? undefined : () => void requestExplain()} /> : null}
  </div>;
}
