/**
 * Ask bus — lets a surface hand a prompt to the ⌘J chat overlay.
 *
 * AnalysisSources' follow-up chips live inside the newsfeed lightbox, which is
 * portalled to document.body and has no path to ChatLauncher's local `open`
 * state. Rather than lift chat state into a global provider for one caller,
 * this is a two-function DOM event bus: the chip emits, the launcher listens,
 * opens itself and sends the prompt.
 *
 * Deliberately DOM-event based (not a module-level singleton) so it survives
 * fast-refresh and stays inert during SSR.
 */

const ASK_EVENT = "radon:ask";

export type AskDetail = { prompt: string };

/** Fire-and-forget. No-op during SSR, where there is no chat mounted anyway. */
export function emitAsk(prompt: string): void {
  const cleaned = prompt.trim();
  if (!cleaned || typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent<AskDetail>(ASK_EVENT, { detail: { prompt: cleaned } }));
}

/** Returns an unsubscribe fn; safe to call during SSR (returns a no-op). */
export function subscribeAsk(handler: (prompt: string) => void): () => void {
  if (typeof window === "undefined") return () => {};
  const listener = (event: Event) => {
    const detail = (event as CustomEvent<AskDetail>).detail;
    if (detail?.prompt) handler(detail.prompt);
  };
  window.addEventListener(ASK_EVENT, listener);
  return () => window.removeEventListener(ASK_EVENT, listener);
}
