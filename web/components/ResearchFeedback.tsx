"use client";
import ErrorToast from "@/components/ErrorToast";
import { useCallback, useId, useRef, useState, type MouseEvent } from "react";
import { usePointerPresence } from "@/lib/usePointerPresence";
import { ThumbsDown, ThumbsUp } from "lucide-react";
import { FEEDBACK_REASONS, MAX_FEEDBACK_COMMENT, REASONS_FOR_VOTE, type FeedbackReason, type PostFeedback } from "@/lib/researchFeedback";
import styles from "./ResearchFeedback.module.css";

type Props = {
  /** A published research post. */
  postId?: string;
  /** A held or dropped document: up means "should have published", down confirms the hold. No reason chips. */
  workKey?: string;
  initial?: PostFeedback;
  /** Post: fired after a saved thumbs-down. Held document: fired after any saved vote. */
  onHidden: (id: string) => void;
};

/** Operator ground truth for research items: a vote, reason chips and an optional comment, sent only on Save. */
export default function ResearchFeedback({ postId, workKey, initial, onHidden }: Props) {
  const held = workKey !== undefined;
  const target = (held ? workKey : postId) as string;
  const [saved, setSaved] = useState<PostFeedback | undefined>(initial);
  const [draft, setDraft] = useState<"up" | "down" | null>(null);
  const [reasons, setReasons] = useState<FeedbackReason[]>([]);
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const commentId = useId();
  const { open: expanded, mounted, setOpen, props: fade } = usePointerPresence("panel");
  const voteTrigger = useRef<HTMLButtonElement | null>(null);

  const open = (vote: "up" | "down", event: MouseEvent<HTMLButtonElement>) => {
    voteTrigger.current = event.currentTarget;
    if (expanded && draft === vote) {
      setOpen(false, event.detail > 0);
      event.currentTarget.focus();
      return;
    }
    setMessage("");
    setDraft(vote);
    setOpen(true, event.detail > 0);
    setReasons(saved?.vote === vote ? saved.reasons : []);
    setComment(saved?.vote === vote ? saved.comment : "");
  };

  const save = useCallback(async () => {
    if (!expanded || !draft || busy) return;
    setBusy(true);
    setError("");
    try {
      const response = await fetch("/api/newsfeed/research/feedback", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        cache: "no-store",
        body: JSON.stringify({ ...(held ? { workKey } : { postId }), vote: draft, reasons, comment: comment.trim() }),
      });
      if (!response.ok) {
        const body = await response.json().catch(() => null) as { error?: string } | null;
        throw new Error(body?.error || "Could not save feedback.");
      }
      setSaved({ vote: draft, reasons, comment: comment.trim() });
      setMessage("Saved");
      if (held || draft === "down") onHidden(target);
      else setOpen(false);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not save feedback.");
    } finally {
      setBusy(false);
    }
  }, [busy, comment, draft, expanded, held, onHidden, postId, reasons, target, workKey, setOpen]);

  return <div className={styles.root} data-research-feedback onKeyDown={event => {
    if (event.key === "Escape" && expanded) { event.stopPropagation(); setOpen(false); voteTrigger.current?.focus(); }
    if (event.key === "ArrowLeft" || event.key === "ArrowRight") event.stopPropagation();
  }} onTouchStart={event => event.stopPropagation()} onTouchEnd={event => event.stopPropagation()}>
    <div className={styles.votes}>
      <button type="button" className={styles.vote} aria-label={held ? "Should have published" : "Thumbs up"} aria-pressed={(expanded ? draft : saved?.vote) === "up"} aria-expanded={expanded && draft === "up"} onClick={event => open("up", event)}>
        <ThumbsUp size={15} aria-hidden />{held ? <span>Should have published</span> : null}
      </button>
      <button type="button" className={styles.vote} aria-label={held ? "Correct to hold" : "Thumbs down"} aria-pressed={(expanded ? draft : saved?.vote) === "down"} aria-expanded={expanded && draft === "down"} onClick={event => open("down", event)}>
        <ThumbsDown size={15} aria-hidden />{held ? <span>Correct to hold</span> : null}
      </button>
      <span role="status" className={styles.status}>{message}</span>
    </div>
    {mounted && draft ? <section {...fade} className={`pointer-fade ${styles.panel}`} aria-label={draft === "up" ? "Thumbs up feedback" : "Thumbs down feedback"}>
      <div className={styles.chips} hidden={held}>
        {(held ? [] : REASONS_FOR_VOTE[draft]).map(reason => (
          <button key={reason} type="button" className={styles.chip} aria-pressed={reasons.includes(reason)}
            onClick={() => setReasons(current => current.includes(reason) ? current.filter(r => r !== reason) : [...current, reason])}>
            {FEEDBACK_REASONS[reason]}
          </button>
        ))}
      </div>
      <label className={styles.label} htmlFor={commentId}>Comment (optional)</label>
      <textarea id={commentId} value={comment} maxLength={MAX_FEEDBACK_COMMENT} onChange={event => setComment(event.target.value)}
        placeholder={held
          ? (draft === "up" ? "What the item should have said, or where the number is in the PDF" : "Why this document does not belong in the feed")
          : (draft === "up" ? "What made this useful, or what is missing (a chart, more detail)" : "Why this should not have been published")} />
      <div className={styles.actions}>
        <button type="button" className={styles.save} disabled={busy} onClick={save}>Save feedback</button>
        {!held && draft === "down" ? <span className={styles.note}>Saving hides this item from your feed.</span> : null}
      </div>
    </section> : null}
    {error ? <ErrorToast message={error} onRetry={busy ? undefined : () => { void save(); }} /> : null}
  </div>;
}
