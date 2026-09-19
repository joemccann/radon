import { createHash } from "node:crypto";
import { requireRouteAccess } from "@/lib/routeAccess";
import { dbExecute } from "@/lib/dbExecute";
import type { HeldDocument, HeldDraft } from "@/lib/researchFeedback";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export const radonCapability = "internal";

/** The operator reviews a small daily sample, not the whole hold pile. */
const DAILY_SAMPLE = 10;
const LOOKBACK_DAYS = 7;

function json(body: unknown, status = 200): Response {
  return Response.json(body, { status, headers: { "Cache-Control": "private, no-store" } });
}

function parseJson<T>(value: unknown, fallback: T): T {
  try { return JSON.parse(String(value ?? "")) as T; } catch { return fallback; }
}

/** Round-robin across the primary reason code so rare hold reasons are always represented; order within a code is
 * a hash of (day, work key), so the sample is stable all day and changes tomorrow. */
function dailySample(documents: HeldDocument[], day: string): HeldDocument[] {
  const rank = (doc: HeldDocument) => createHash("sha256").update(day + "\0" + doc.workKey).digest("hex");
  const groups = new Map<string, HeldDocument[]>();
  for (const doc of documents) {
    const code = doc.reasonCodes[0] ?? "UNKNOWN";
    groups.set(code, [...(groups.get(code) ?? []), doc]);
  }
  const queues = [...groups.entries()].sort((a, b) => a[1].length - b[1].length || a[0].localeCompare(b[0]))
    .map(([, docs]) => docs.sort((a, b) => rank(a).localeCompare(rank(b))));
  const sample: HeldDocument[] = [];
  while (sample.length < DAILY_SAMPLE && queues.some((queue) => queue.length)) {
    for (const queue of queues) {
      const next = queue.shift();
      if (next) sample.push(next);
      if (sample.length === DAILY_SAMPLE) break;
    }
  }
  return sample;
}

export async function GET(request: Request): Promise<Response> {
  const access = await requireRouteAccess(request, {
    operatorOnly: true,
    rate: { key: "research-held", limit: 60, windowMs: 60_000 },
    durableRateTier: "A",
  });
  if (!access.ok) return access.response;
  try {
    // Probe without raising: a SQL error resets the pooled client, which an unmigrated additive table must not do.
    const probe = await dbExecute({
      sql: "SELECT count(*) AS n FROM sqlite_master WHERE type = 'table' AND name IN ('research_outcomes', 'research_feedback')", args: [],
    }, { label: "research-held-probe" });
    if (Number(probe.rows[0]?.n ?? 0) < 2) return json({ items: [], pending: 0 });

    const day = new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York" }).format(new Date());
    const cutoff = new Date(Date.now() - LOOKBACK_DAYS * 86_400_000).toISOString().slice(0, 10);
    const result = await dbExecute({
      sql: `SELECT o.work_key, o.file_name, o.publisher, o.series, o.doc_type, o.folder_date, o.document_date, o.outcome, o.reason_codes, o.drafts_json
            FROM research_outcomes o
            WHERE o.outcome IN ('held', 'dropped') AND o.folder_date >= ?
              AND NOT EXISTS (
                SELECT 1 FROM research_feedback f WHERE f.target = 'held' AND f.work_key = o.work_key AND f.vote != 'clear'
                  AND f.rowid = (SELECT g.rowid FROM research_feedback g WHERE g.target = 'held' AND g.work_key = o.work_key
                                 ORDER BY g.created_at DESC, g.rowid DESC LIMIT 1))
            ORDER BY o.folder_date DESC LIMIT 1000`,
      args: [cutoff],
    }, { timeoutMs: 8_000, label: "research-held" });
    const documents = result.rows.map((row): HeldDocument => ({
      workKey: String(row.work_key), fileName: String(row.file_name), publisher: String(row.publisher), series: String(row.series),
      docType: String(row.doc_type), folderDate: String(row.folder_date), documentDate: String(row.document_date),
      outcome: row.outcome === "dropped" ? "dropped" : "held",
      reasonCodes: parseJson<string[]>(row.reason_codes, []).filter((code) => typeof code === "string"),
      drafts: parseJson<HeldDraft[]>(row.drafts_json, []).filter((draft) => draft && typeof draft === "object").slice(0, 8),
    }));
    return json({ items: dailySample(documents, day), pending: documents.length });
  } catch {
    return json({ error: "Held review temporarily unavailable." }, 503);
  }
}
