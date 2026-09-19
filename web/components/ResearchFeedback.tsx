"use client";
import ErrorToast from "@/components/ErrorToast";
import { useCallback, useId, useState } from "react";
import { ThumbsDown, ThumbsUp } from "lucide-react";
import { FEEDBACK_REASONS, MAX_FEEDBACK_COMMENT, REASONS_FOR_VOTE, type FeedbackReason, type PostFeedback } from "@/lib/researchFeedback";
import styles from "./ResearchFeedback.module.css";

type Props = { postId: string; initial?: PostFeedback; onHidden: (postId: string) => void };

/** Operator ground truth for research feed items: a vote, reason chips and an optional comment, sent only on Save. */
export default function ResearchFeedback({ postId, initial, onHidden }: Props) {
  const [saved, setSaved] = useState<PostFeedback | undefined>(initial);
  const [draft, setDraft] = useState<"up" | "down" | null>(null);
  const [reasons, setReasons] = useState<FeedbackReason[]>([]);
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const commentId = useId();

  const open = (vote: "up" | "down") => {
    setMessage("");
    setDraft(current => current === vote ? null : vote);
    setReasons(saved?.vote === vote ? saved.reasons : []);
    setComment(saved?.vote === vote ? saved.comment : "");
  };

  const save = useCallback(async () => {
    if (!draft || busy) return;
    setBusy(true);
    setError("");
    try {
      const response = await fetch("/api/newsfeed/research/feedback", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        cache: "no-store",
        body: JSON.stringify({ postId, vote: draft, reasons, comment: comment.trim() }),
      });
      if (!response.ok) {
        const body = await response.json().catch(() => null) as { error?: string } | null;
        throw new Error(body?.error || "Could not save feedback.");
      }
      setSaved({ vote: draft, reasons, comment: comment.trim() });
      setMessage("Saved");
      if (draft === "down") onHidden(postId);
      else setDraft(null);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not save feedback.");
    } finally {
      setBusy(false);
    }
  }, [busy, comment, draft, onHidden, postId, reasons]);

  return <div className={styles.root} data-research-feedback onKeyDown={event => {
    if (event.key === "ArrowLeft" || event.key === "ArrowRight") event.stopPropagation();
  }} onTouchStart={event => event.stopPropagation()} onTouchEnd={event => event.stopPropagation()}>
    <div className={styles.votes}>
      <button type="button" className={styles.vote} aria-label="Thumbs up" aria-pressed={(draft ?? saved?.vote) === "up"} onClick={() => open("up")}>
        <ThumbsUp size={15} aria-hidden />
      </button>
      <button type="button" className={styles.vote} aria-label="Thumbs down" aria-pressed={(draft ?? saved?.vote) === "down"} onClick={() => open("down")}>
        <ThumbsDown size={15} aria-hidden />
      </button>
      <span role="status" className={styles.status}>{message}</span>
    </div>
    {draft ? <section className={styles.panel} aria-label={draft === "up" ? "Thumbs up feedback" : "Thumbs down feedback"}>
      <div className={styles.chips}>
        {REASONS_FOR_VOTE[draft].map(reason => (
          <button key={reason} type="button" className={styles.chip} aria-pressed={reasons.includes(reason)}
            onClick={() => setReasons(current => current.includes(reason) ? current.filter(r => r !== reason) : [...current, reason])}>
            {FEEDBACK_REASONS[reason]}
          </button>
        ))}
      </div>
      <label className={styles.label} htmlFor={commentId}>Comment (optional)</label>
      <textarea id={commentId} value={comment} maxLength={MAX_FEEDBACK_COMMENT} onChange={event => setComment(event.target.value)}
        placeholder={draft === "up" ? "What made this useful, or what is missing (a chart, more detail)" : "Why this should not have been published"} />
      <div className={styles.actions}>
        <button type="button" className={styles.save} disabled={busy} onClick={save}>Save feedback</button>
        {draft === "down" ? <span className={styles.note}>Saving hides this item from your feed.</span> : null}
      </div>
    </section> : null}
    {error ? <ErrorToast message={error} onRetry={busy ? undefined : () => { void save(); }} /> : null}
  </div>;
}
