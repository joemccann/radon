export interface McpEvaluationReport {
  schema_version: 1; mode: "offline-contracts" | "live-public"; generated_at: string;
  queries: { query: string; passed: boolean; latency_ms: number; error_class: string | null }[];
  metrics: { total: number; passed: number; failed: number; contract_accuracy: number; p50_ms: number; p95_ms: number };
}
function object(value: unknown): value is Record<string, unknown> { return !!value && typeof value === "object" && !Array.isArray(value); }
export function parseMcpEvaluationReport(value: unknown): McpEvaluationReport {
  if (!object(value) || value.schema_version !== 1 || !["offline-contracts", "live-public"].includes(String(value.mode))
    || typeof value.generated_at !== "string" || !/^\d{4}-\d{2}-\d{2}T/.test(value.generated_at) || !Number.isFinite(Date.parse(value.generated_at))
    || !Array.isArray(value.queries) || !value.queries.length || value.queries.length > 500 || !object(value.metrics)) throw new Error("Expected a version 1 MCP evaluator report with a mode, timestamp, and query results.");
  const queries = value.queries.map((row: unknown) => {
    if (!object(row) || typeof row.query !== "string" || !row.query.trim() || row.query.length > 1000 || typeof row.passed !== "boolean"
      || typeof row.latency_ms !== "number" || !Number.isFinite(row.latency_ms) || row.latency_ms < 0
      || !(row.error_class === null || (typeof row.error_class === "string" && row.error_class.length <= 500))) throw new Error("Each query requires a name, pass/fail result, and finite nonnegative latency.");
    return { query: row.query, passed: row.passed, latency_ms: row.latency_ms, error_class: row.error_class as string | null };
  });
  if (new Set(queries.map((row) => row.query)).size !== queries.length) throw new Error("Evaluation query names must be unique.");
  const durations = queries.map((row) => row.latency_ms).sort((a, b) => a - b);
  const passed = queries.filter((row) => row.passed).length;
  const metrics = { total: queries.length, passed, failed: queries.length - passed, contract_accuracy: passed / queries.length,
    p50_ms: durations[Math.ceil(queries.length * .5) - 1], p95_ms: durations[Math.ceil(queries.length * .95) - 1] };
  for (const [key, expected] of Object.entries(metrics)) {
    const actual = value.metrics[key];
    if (typeof actual !== "number" || !Number.isFinite(actual) || Math.abs(actual - expected) > 1e-6) throw new Error(`Report ${key} disagrees with its query results.`);
  }
  return { schema_version: 1, mode: value.mode as McpEvaluationReport["mode"], generated_at: value.generated_at, queries, metrics };
}
