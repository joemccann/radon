import { requireRouteAccess } from "@/lib/routeAccess";
import { dbExecute } from "@/lib/dbExecute";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
// The operator alone decides a triage rule; chat can neither read nor decide one.
export const radonCapability = { GET: "internal", POST: "internal" };

const PROPOSAL_ID = /^(series_deny|publisher_deny|doc_type_drop):.{1,200}$/;
const DECISIONS = { approve: "approved", reject: "rejected", revoke: "rejected" } as const;

const EMPTY = { proposed: [] as const, approved: [] as const, rejected: [] as const };

function json(body: unknown, status = 200): Response {
  return Response.json(body, { status, headers: { "Cache-Control": "private, no-store" } });
}

function toRule(row: any) {
  let evidence = 0;
  try { const parsed = JSON.parse(String(row.evidence_json ?? "[]")); evidence = Array.isArray(parsed) ? parsed.length : 0; } catch { /* count stays 0 */ }
  return { status: String(row.status), rule: { id: String(row.id), kind: String(row.kind), key: String(row.key), downs: Number(row.downs), ups: Number(row.ups), evidence } };
}

async function tableExists(): Promise<boolean> {
  // Probe without raising: a SQL error resets the pooled client, which an unmigrated additive table must not do.
  const probe = await dbExecute({ sql: "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'research_rule_proposals'", args: [] },
    { label: "research-rules-probe" });
  return probe.rows.length > 0;
}

export async function GET(request: Request): Promise<Response> {
  const access = await requireRouteAccess(request, { operatorOnly: true, rate: { key: "research-rules", limit: 60, windowMs: 60_000 }, durableRateTier: "A" });
  if (!access.ok) return access.response;
  try {
    if (!(await tableExists())) return json(EMPTY);
    const result = await dbExecute({
      sql: "SELECT id, kind, key, downs, ups, evidence_json, status FROM research_rule_proposals WHERE status IN ('proposed', 'approved') ORDER BY created_at DESC LIMIT 200",
      args: [],
    }, { label: "research-rules" });
    const rejectedResult = await dbExecute({
      sql: "SELECT id, kind, key, downs, ups, evidence_json, status FROM research_rule_proposals WHERE status = 'rejected' ORDER BY decided_at DESC LIMIT 50",
      args: [],
    }, { label: "research-rules-rejected" });
    const rows = result.rows.map(toRule);
    return json({
      proposed: rows.filter((r) => r.status === "proposed").map((r) => r.rule),
      approved: rows.filter((r) => r.status === "approved").map((r) => r.rule),
      rejected: rejectedResult.rows.map((row) => toRule(row).rule),
    });
  } catch {
    return json({ error: "Rule proposals temporarily unavailable." }, 503);
  }
}

export async function POST(request: Request): Promise<Response> {
  const access = await requireRouteAccess(request, { operatorOnly: true, rate: { key: "research-rules-decide", limit: 30, windowMs: 60_000 }, durableRateTier: "D" });
  if (!access.ok) return access.response;
  let body: { id?: unknown; decision?: unknown };
  try { body = JSON.parse((await request.text()).slice(0, 2_000)); } catch { return json({ error: "Provide a valid decision." }, 400); }
  if (typeof body?.id !== "string" || !PROPOSAL_ID.test(body.id)) return json({ error: "Invalid rule id." }, 400);
  if (typeof body.decision !== "string" || !(body.decision in DECISIONS)) return json({ error: "Invalid decision." }, 400);
  const status = DECISIONS[body.decision as keyof typeof DECISIONS];
  try {
    const result = await dbExecute({
      sql: "UPDATE research_rule_proposals SET status = ?, decided_at = ?, decided_by = ? WHERE id = ?",
      args: [status, new Date().toISOString(), access.principal.userId, body.id],
    }, { label: "research-rules-decide" });
    if (!result.rowsAffected) return json({ error: "Rule proposal not found." }, 404);
    return json({ id: body.id, status });
  } catch {
    return json({ error: "Rule proposals temporarily unavailable." }, 503);
  }
}
