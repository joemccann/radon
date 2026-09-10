import { beforeEach, describe, expect, it, vi } from "vitest";
const access = vi.hoisted(() => vi.fn());
const db = vi.hoisted(() => vi.fn());
vi.mock("@/lib/routeAccess", () => ({ requireRouteAccess: access }));
vi.mock("@/lib/dbExecute", () => ({ dbExecute: db }));
import { GET } from "@/app/api/research/governance/route";
import { AUDIT_EXPORT_LIMIT, readGovernanceReport } from "@/lib/assistant/governance";

beforeEach(() => {
  vi.clearAllMocks();
  access.mockResolvedValue({ ok: true, principal: { kind: "operator", userId: "fixture" } });
  db.mockResolvedValue({ rows: [] });
});

describe("governance export", () => {
  it("denies nonoperators before any database reads", async () => {
    access.mockResolvedValue({ ok: false, response: new Response(null, { status: 403 }) });
    expect((await GET(new Request("https://app.radon.run/api/research/governance"))).status).toBe(403);
    expect(db).not.toHaveBeenCalled();
  });
  it("returns private download and separates absent audit from unavailable storage", async () => {
    db.mockResolvedValueOnce({ rows: [] }).mockRejectedValueOnce(new Error("token=private"));
    const request = new Request("https://app.radon.run/api/research/governance?download=1");
    const response = await GET(request);
    const body = await response.json();
    expect(access).toHaveBeenCalledWith(request, { operatorOnly: true });
    expect(response.headers.get("Cache-Control")).toBe("private, no-store");
    expect(response.headers.get("Content-Disposition")).toContain("attachment");
    expect(body.audit.orders).toMatchObject({ status: "available", rows: [] });
    expect(body.audit.assistant.status).toBe("unavailable");
    expect(JSON.stringify(body)).not.toContain("token=private");
    expect(body.reliability.status).toBe("not-measured");
  });
  it("whitelists audit fields, bounds reads, preserves signed amounts and marks truncation", async () => {
    db.mockResolvedValue({ rows: Array.from({ length: AUDIT_EXPORT_LIMIT + 1 }, (_, id) => ({ id: BigInt(id), limit_price: -1.25, detail: "secret", user_msg: "secret", tool_calls: "secret", account: "secret" })) });
    const report = await readGovernanceReport();
    expect(report.audit.orders.truncated).toBe(true);
    expect(report.audit.orders.rows).toHaveLength(AUDIT_EXPORT_LIMIT);
    expect(report.audit.orders.rows[0]).toMatchObject({ id: "0", limit_price: -1.25 });
    expect(JSON.stringify(report)).not.toContain('"secret"');
    expect(db.mock.calls.every(([statement, options]) => statement.args[0] === 201 && options.timeoutMs === 3000)).toBe(true);
    expect(report.limitations.some(item => item.includes("not a complete"))).toBe(true);
  });
});
