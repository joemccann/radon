import { dbExecute } from "@/lib/dbExecute";

export const GOVERNANCE_POLICY = [
  { surface: "Hosted MCP", roles: ["public", "demo", "operator"], action: "Read by role", control: "Default deny. Caller token reauthenticated upstream. No write tools.", evidence: "scripts/mcp_hosted/server.py" },
  { surface: "Assistant API catalog", roles: ["operator"], action: "Allowlisted research and workspace actions", control: "Trading mutations and admin actions refused by capability catalog.", evidence: "web/lib/assistant/catalog.ts" },
  { surface: "Assistant order proposal", roles: ["operator"], action: "Prepare only", control: "Current-turn order intent and validated instrument required. Destructive calls stop the loop for confirmation.", evidence: "web/lib/assistant/loop.ts" },
  { surface: "Order ticket", roles: ["operator"], action: "Confirm and submit to IB", control: "OrderRiskGate, authenticated placement, server order limits and idempotency. Research handoff does not submit.", evidence: "web/app/api/orders/place/route.ts" },
  { surface: "Demo trading", roles: ["demo"], action: "Paper only", control: "Demo blockade prevents live placement; unknown authentication fails closed.", evidence: "web/lib/demo/orderBlockade.ts" },
] as const;

const ORDER_COLUMNS = ["id", "event_type", "order_ref", "order_id", "perm_id", "symbol", "action", "quantity", "limit_price", "status", "created_at"] as const;
const ASSISTANT_COLUMNS = ["id", "ts", "rounds", "outcome", "image_count", "provider", "model"] as const;
export const AUDIT_EXPORT_LIMIT = 200;

type AuditSection = { status: "available" | "unavailable"; rows: Record<string, string | number | null>[]; truncated: boolean; error?: string };

async function readSection(table: "order_events" | "assistant_turns", columns: readonly string[]): Promise<AuditSection> {
  try {
    const result = await dbExecute({
      sql: `SELECT ${columns.join(", ")} FROM ${table} ORDER BY id DESC LIMIT ?`,
      args: [AUDIT_EXPORT_LIMIT + 1],
    }, { timeoutMs: 3_000, label: `governance-${table}` });
    const rows = result.rows.slice(0, AUDIT_EXPORT_LIMIT).map(row => Object.fromEntries(columns.map(column => {
      const value = row[column];
      return [column, typeof value === "bigint" ? value.toString() : typeof value === "number" ? (Number.isFinite(value) ? value : null) : typeof value === "string" ? value : null];
    })));
    return { status: "available", rows, truncated: result.rows.length > AUDIT_EXPORT_LIMIT };
  } catch {
    return { status: "unavailable", rows: [], truncated: false, error: "Audit storage unavailable" };
  }
}

export async function readGovernanceReport() {
  const [orders, assistant] = await Promise.all([
    readSection("order_events", ORDER_COLUMNS), readSection("assistant_turns", ASSISTANT_COLUMNS),
  ]);
  return {
    schemaVersion: 1,
    generatedAt: new Date().toISOString(),
    policy: GOVERNANCE_POLICY,
    audit: { orders, assistant },
    reliability: {
      status: "not-measured" as const,
      command: "PYTHONPATH=scripts python3.13 -m mcp_hosted.evaluate --live --output data/mcp-evaluation.json",
      description: "Golden-query evaluation has offline contract and public live modes. No operational report is attached to this audit export.",
    },
    limitations: [
      `Latest ${AUDIT_EXPORT_LIMIT} rows per stream. Truncated streams require a database export for full history.`,
      "Order and assistant telemetry writes are best-effort; this export is not a complete or tamper-evident compliance ledger.",
      "Streams are independent. A proposal row does not prove operator confirmation or identify a corresponding order.",
      "Raw prompts, tool arguments, broker error detail and account fields are excluded.",
    ],
  };
}
