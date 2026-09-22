import { describe, expect, it } from "vitest";
import { parseMcpEvaluationReport } from "../lib/researchWorkbench/reliability";
const fixture = () => ({ schema_version: 1, mode: "offline-contracts", generated_at: "2026-09-10T15:00:00Z", metrics: { total: 2, passed: 1, failed: 1, contract_accuracy: .5, p50_ms: 1, p95_ms: 5 }, queries: [{ query: "identity", passed: true, latency_ms: 1, error_class: null }, { query: "deny", passed: false, latency_ms: 5, error_class: "AssertionError" }] });
describe("MCP evaluation import", () => {
  it("retains failures and distinguishes fixture latency from live measurement", () => { const report = parseMcpEvaluationReport(fixture()); expect(report.mode).toBe("offline-contracts"); expect(report.metrics.failed).toBe(1); expect(report.metrics.p95_ms).toBe(5); });
  it("rejects a falsified all-green summary", () => { const raw = fixture(); raw.metrics.passed = 2; expect(() => parseMcpEvaluationReport(raw)).toThrow(/disagrees/); });
  it("rejects negative or unbounded timing data", () => { for (const latency_ms of [-1, Infinity, NaN]) { const raw = fixture(); raw.queries[0].latency_ms = latency_ms; expect(() => parseMcpEvaluationReport(raw)).toThrow(/nonnegative/); } });
  it("rejects repeated queries and empty suites", () => { const raw = fixture(); raw.queries[1].query = raw.queries[0].query; expect(() => parseMcpEvaluationReport(raw)).toThrow(/unique/); expect(() => parseMcpEvaluationReport({ ...fixture(), queries: [] })).toThrow(/version 1/); });
});
