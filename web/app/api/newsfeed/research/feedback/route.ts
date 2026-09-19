import { randomUUID } from "node:crypto";
import { requireRouteAccess } from "@/lib/routeAccess";
import { dbExecute } from "@/lib/dbExecute";
import { invalidateCache } from "@/lib/dbCache";
import { parseFeedback } from "@/lib/researchFeedback";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
// Ground-truth labels come from the operator's own hands, never from chat.
export const radonCapability = "internal";

function json(body: unknown, status = 200): Response {
  return Response.json(body, { status, headers: { "Cache-Control": "no-store" } });
}

function parseArray(value: unknown): string[] {
  try {
    const parsed = JSON.parse(String(value ?? "[]"));
    return Array.isArray(parsed) ? parsed.filter((item) => typeof item === "string") : [];
  } catch { return []; }
}

export async function POST(request: Request): Promise<Response> {
  const access = await requireRouteAccess(request, {
    operatorOnly: true,
    rate: { key: "research-feedback", limit: 60, windowMs: 60_000 },
    durableRateTier: "D",
  });
  if (!access.ok) return access.response;

  let parsed;
  try {
    const raw = await request.text();
    if (raw.length > 10_000) return json({ error: "Feedback is too long." }, 400);
    parsed = parseFeedback(JSON.parse(raw));
  } catch { return json({ error: "Provide a valid feedback body." }, 400); }
  if ("invalid" in parsed) return json({ error: `Invalid ${parsed.invalid}.` }, 400);

  try {
    const id = randomUUID();
    const now = new Date().toISOString();
    if (parsed.workKey) {
      // A held or dropped document: thumbs-up means "should have published", thumbs-down confirms the hold.
      const found = await dbExecute({
        sql: `SELECT file_id, file_name, publisher, series, doc_type, folder_date, document_date, outcome, reason_codes, pipeline
              FROM research_outcomes WHERE work_key = ? AND outcome IN ('held', 'dropped')`,
        args: [parsed.workKey],
      }, { label: "research-feedback-outcome" });
      const row = found.rows[0];
      if (!row) return json({ error: "Held document not found." }, 404);
      const snapshot = {
        fileName: String(row.file_name), publisher: String(row.publisher), series: String(row.series), docType: String(row.doc_type),
        folderDate: String(row.folder_date), documentDate: String(row.document_date), outcome: String(row.outcome),
        reasonCodes: parseArray(row.reason_codes), pipeline: String(row.pipeline),
      };
      await dbExecute({
        sql: `INSERT INTO research_feedback (id, target, work_key, file_id, vote, reasons, comment, actor, snapshot_json, created_at)
              VALUES (?, 'held', ?, ?, ?, ?, ?, ?, ?, ?)`,
        args: [id, parsed.workKey, String(row.file_id ?? ""), parsed.vote, JSON.stringify(parsed.reasons), parsed.comment,
          access.principal.userId, JSON.stringify(snapshot), now],
      }, { label: "research-feedback-write" });
      return json({ id, workKey: parsed.workKey, vote: parsed.vote });
    }

    const found = await dbExecute({
      sql: `SELECT p.title, p.tags, r.provenance_json FROM posts p
            JOIN research_post_sources r ON r.post_id = p.id WHERE p.id = ?`,
      args: [parsed.postId as string],
    }, { label: "research-feedback-post" });
    const row = found.rows[0];
    if (!row) return json({ error: "Research post not found." }, 404);

    let provenance: Record<string, unknown> = {};
    try { provenance = JSON.parse(String(row.provenance_json)) ?? {}; } catch { /* snapshot stays minimal */ }
    // Freeze what the operator saw; the learner never re-reads a post that may since have been updated.
    const snapshot = {
      title: String(row.title), tags: parseArray(row.tags),
      publisher: provenance.publisher, series: provenance.series, docType: provenance.docType,
      documentDate: provenance.documentDate, folderDate: provenance.folderDate, pages: provenance.pages,
      figures: Array.isArray(provenance.figures) ? provenance.figures.length : 0, pipeline: provenance.pipeline ?? "v1",
    };
    await dbExecute({
      sql: `INSERT INTO research_feedback (id, target, post_id, file_id, vote, reasons, comment, actor, snapshot_json, created_at)
            VALUES (?, 'post', ?, ?, ?, ?, ?, ?, ?, ?)`,
      args: [id, parsed.postId as string, typeof provenance.fileId === "string" ? provenance.fileId : null, parsed.vote,
        JSON.stringify(parsed.reasons), parsed.comment, access.principal.userId, JSON.stringify(snapshot), now],
    }, { label: "research-feedback-write" });
    // A thumbs-down must leave the feed on the next read, not after the 30s posts cache.
    invalidateCache("newsfeed:posts");
    return json({ id, postId: parsed.postId, vote: parsed.vote, hidden: parsed.vote === "down" });
  } catch {
    return json({ error: "Feedback store temporarily unavailable." }, 503);
  }
}
