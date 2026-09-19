/** Operator feedback on research feed items: shared vocabulary for the route, the feed and the UI. */
export const FEEDBACK_VOTES = ["up", "down", "clear"] as const;
export type FeedbackVote = (typeof FEEDBACK_VOTES)[number];

export const FEEDBACK_REASONS = {
  not_relevant: "Not relevant to me",
  wrong: "Wrong or misleading",
  stale_duplicate: "Stale or duplicate",
  badly_written: "Badly written",
  want_more: "Want more (chart, detail)",
} as const;
export type FeedbackReason = keyof typeof FEEDBACK_REASONS;

/** Chips offered per vote; a thumbs-up can still ask for more. */
export const REASONS_FOR_VOTE: Record<"up" | "down", FeedbackReason[]> = {
  up: ["want_more"],
  down: ["not_relevant", "wrong", "stale_duplicate", "badly_written"],
};

export const MAX_FEEDBACK_COMMENT = 2000;
export const RESEARCH_POST_ID = /^research-[a-f0-9]{32,64}$/;
export const RESEARCH_WORK_KEY = /^[a-f0-9]{64}$/;

export type PostFeedback = { vote: "up" | "down"; reasons: FeedbackReason[]; comment: string };

/** A vote targets a published post (postId) or a held/dropped document (workKey), never both. */
export type ParsedFeedback = { postId?: string; workKey?: string; vote: FeedbackVote; reasons: FeedbackReason[]; comment: string };

export type HeldDraft = { title: string; content: string; held: string; detail: string };
/** What the document is, so a hold with no drafts can still be judged. */
export type HeldContext = { pageCount: number | null; figureCount: number; dateSource: string; excerpt: string; selectorReason: string; sourceUrl: string };
export type HeldDocument = {
  context: HeldContext;
  workKey: string; fileName: string; publisher: string; series: string; docType: string; folderDate: string; documentDate: string;
  outcome: "held" | "dropped"; reasonCodes: string[]; drafts: HeldDraft[];
};

/** Returns the validated vote or the name of the first invalid field. */
export function parseFeedback(value: unknown): ParsedFeedback | { invalid: string } {
  if (!value || typeof value !== "object") return { invalid: "body" };
  const body = value as Record<string, unknown>;
  const hasPost = body.postId !== undefined, hasWork = body.workKey !== undefined;
  if (hasPost === hasWork) return { invalid: hasPost ? "target" : "post id" };
  if (hasPost && (typeof body.postId !== "string" || !RESEARCH_POST_ID.test(body.postId))) return { invalid: "post id" };
  if (hasWork && (typeof body.workKey !== "string" || !RESEARCH_WORK_KEY.test(body.workKey))) return { invalid: "work key" };
  if (typeof body.vote !== "string" || !(FEEDBACK_VOTES as readonly string[]).includes(body.vote)) return { invalid: "vote" };
  const reasons = body.reasons ?? [];
  if (!Array.isArray(reasons) || reasons.length > 5
    || !reasons.every((reason) => typeof reason === "string" && reason in FEEDBACK_REASONS)) return { invalid: "reason" };
  const comment = body.comment ?? "";
  if (typeof comment !== "string" || comment.length > MAX_FEEDBACK_COMMENT) return { invalid: "comment" };
  return { ...(hasPost ? { postId: body.postId as string } : { workKey: body.workKey as string }), vote: body.vote as FeedbackVote, reasons: [...new Set(reasons)] as FeedbackReason[], comment: comment.trim() };
}
