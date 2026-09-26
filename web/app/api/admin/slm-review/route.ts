import { requireRouteAccess } from "@/lib/routeAccess";
import { dbExecute } from "@/lib/dbExecute";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export const radonCapability = "admin";

const SCHEMA = "radon.slm-review-decisions.v1";
const ALIASES = new Set(["Candidate 1", "Candidate 2", "Candidate 3"]);
const RUN_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
const ITEM_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
const MAX_BODY = 200_000;

type Decision = {
  id: string;
  humanTags: string[];
  acceptance: Record<string, boolean>;
  reviewer: string;
  reviewedAt: string;
};
type Saved = { schema: typeof SCHEMA; runId: string; reviewer: string; decisions: Decision[]; index: number; updatedAt?: string };

function json(body: unknown, status = 200): Response {
  return Response.json(body, { status, headers: { "Cache-Control": "no-store" } });
}

function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function parseDecisions(raw: unknown, reviewer: string): Decision[] | null {
  if (!Array.isArray(raw) || raw.length > 200) return null;
  const seen = new Set<string>();
  const decisions: Decision[] = [];
  for (const value of raw) {
    if (!record(value) || Object.keys(value).sort().join() !== "acceptance,humanTags,id,reviewedAt,reviewer") return null;
    const { id, humanTags, acceptance, reviewedAt } = value;
    if (typeof id !== "string" || !ITEM_ID.test(id) || seen.has(id) || value.reviewer !== reviewer) return null;
    if (!Array.isArray(humanTags) || humanTags.length !== 3 || humanTags.some((tag) => typeof tag !== "string" || tag.length < 1 || tag.length > 80)) return null;
    if (new Set(humanTags.map((tag) => tag.toLowerCase())).size !== 3) return null;
    if (!record(acceptance) || Object.keys(acceptance).some((key) => !ALIASES.has(key) || typeof acceptance[key] !== "boolean")) return null;
    if (typeof reviewedAt !== "string" || !/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$/.test(reviewedAt) || Number.isNaN(Date.parse(reviewedAt))) return null;
    seen.add(id);
    decisions.push({ id, humanTags: humanTags as string[], acceptance: acceptance as Record<string, boolean>, reviewer, reviewedAt });
  }
  return decisions;
}

function parseBody(raw: unknown, runId: string, reviewer: string): Saved | null {
  if (!record(raw) || Object.keys(raw).some((key) => !["schema", "runId", "reviewer", "decisions", "index", "updatedAt"].includes(key))) return null;
  if (raw.schema !== SCHEMA || raw.runId !== runId || raw.reviewer !== reviewer) return null;
  if (!Number.isInteger(raw.index) || (raw.index as number) < 0 || (raw.index as number) > 199) return null;
  const decisions = parseDecisions(raw.decisions, reviewer);
  return decisions ? { schema: SCHEMA, runId, reviewer, decisions, index: raw.index as number } : null;
}

function empty(runId: string, reviewer: string): Saved {
  return { schema: SCHEMA, runId, reviewer, decisions: [], index: 0 };
}

async function read(runId: string, reviewer: string): Promise<{ saved: Saved; revision: number }> {
  const found = await dbExecute({
    sql: "SELECT decisions_json, cursor_index, revision, updated_at FROM slm_review_decisions WHERE run_id = ? AND reviewer = ?",
    args: [runId, reviewer],
  }, { label: "slm-review-read" });
  const row = found.rows[0];
  if (!row) return { saved: empty(runId, reviewer), revision: 0 };
  const decisions = parseDecisions(JSON.parse(String(row.decisions_json)), reviewer);
  if (!decisions) throw new Error("Malformed stored review decisions");
  return {
    saved: { schema: SCHEMA, runId, reviewer, decisions, index: Number(row.cursor_index), updatedAt: String(row.updated_at) },
    revision: Number(row.revision),
  };
}

function merge(prior: Decision[], incoming: Decision[]): Decision[] {
  const byId = new Map(prior.map((decision) => [decision.id, decision]));
  for (const decision of incoming) {
    const existing = byId.get(decision.id);
    if (!existing || decision.reviewedAt >= existing.reviewedAt) byId.set(decision.id, decision);
  }
  return [...byId.values()];
}

function runIdFrom(request: Request): string | null {
  const params = new URL(request.url).searchParams;
  const ids = params.getAll("runId");
  return ids.length === 1 && RUN_ID.test(ids[0]) ? ids[0] : null;
}

export async function GET(request: Request): Promise<Response> {
  const access = await requireRouteAccess(request, { operatorOnly: true });
  if (!access.ok) return access.response;
  const runId = runIdFrom(request);
  if (!runId) return json({ error: "Invalid run ID." }, 400);
  try {
    return json((await read(runId, access.principal.userId)).saved);
  } catch {
    return json({ error: "Review storage temporarily unavailable." }, 503);
  }
}

export async function PUT(request: Request): Promise<Response> {
  const access = await requireRouteAccess(request, {
    operatorOnly: true,
    rate: { key: "slm-review", limit: 120, windowMs: 60_000 },
    durableRateTier: "D",
  });
  if (!access.ok) return access.response;
  const runId = runIdFrom(request);
  if (!runId) return json({ error: "Invalid run ID." }, 400);
  let body: Saved | null;
  try {
    const raw = await request.text();
    if (raw.length > MAX_BODY) return json({ error: "Review decisions are too large." }, 413);
    body = parseBody(JSON.parse(raw), runId, access.principal.userId);
  } catch {
    return json({ error: "Provide valid review decisions." }, 400);
  }
  if (!body) return json({ error: "Provide valid review decisions." }, 400);

  try {
    const reviewer = access.principal.userId;
    await dbExecute({
      sql: "INSERT OR IGNORE INTO slm_review_decisions (run_id, reviewer, updated_at) VALUES (?, ?, ?)",
      args: [runId, reviewer, new Date().toISOString()],
    }, { label: "slm-review-create" });
    for (let attempt = 0; attempt < 5; attempt++) {
      const { saved, revision } = await read(runId, reviewer);
      const decisions = merge(saved.decisions, body.decisions);
      const now = new Date().toISOString();
      const updated = await dbExecute({
        sql: `UPDATE slm_review_decisions SET decisions_json = ?, cursor_index = ?, revision = revision + 1, updated_at = ?
              WHERE run_id = ? AND reviewer = ? AND revision = ?`,
        args: [JSON.stringify(decisions), body.index, now, runId, reviewer, revision],
      }, { label: "slm-review-save" });
      if (updated.rowsAffected === 1) return json({ schema: SCHEMA, runId, reviewer, decisions, index: body.index, updatedAt: now });
    }
    return json({ error: "Review changed concurrently. Retry save." }, 409);
  } catch {
    return json({ error: "Review storage temporarily unavailable." }, 503);
  }
}
