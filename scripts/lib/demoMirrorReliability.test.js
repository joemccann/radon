import { describe, expect, it, vi } from "vitest";

import {
  createBoundedFetch,
  runNewsfeedMirror,
} from "../db/mirror_newsfeed_to_demo.js";
import {
  HISTORY,
  LATEST_ONE,
  PER_KEY,
  PURGED_ACCOUNT_TABLES,
  isTransientTursoError,
  stripAccountDerivedFields,
  runMarketMirror,
} from "../db/mirror_market_snapshots_to_demo.js";


function post(id = "p1") {
  return {
    id,
    title: "Title",
    content: "Body",
    timestamp: "2026-07-11T12:00:00Z",
    images: "[]",
    raw_images: "[]",
    tags: "[]",
    tags_text: "[]",
    tags_vision: "[]",
    created_at: "2026-07-11T12:00:00Z",
    updated_at: "2026-07-11T12:00:00Z",
  };
}

function turso502() {
  const err = new Error("SERVER_ERROR: Server returned HTTP status 502");
  err.code = "SERVER_ERROR";
  err.status = 502;
  return err;
}

function scanRow(service = "gex") {
  return {
    service,
    scan_time: "2026-08-21T21:00:00Z",
    payload: "{}",
  };
}

describe("demo newsfeed mirror reliability", () => {
  it("retries an idempotent transient destination write and records recovery", async () => {
    const src = {
      execute: vi.fn()
        .mockResolvedValueOnce({ rows: [post()] })
        .mockResolvedValue({ rows: [] }),
    };
    const dst = {
      batch: vi.fn()
        .mockRejectedValueOnce(new TypeError("fetch failed"))
        .mockResolvedValueOnce(undefined),
      execute: vi.fn().mockResolvedValue({
        rows: [{ n: 1, newest: "2026-07-11T12:00:00Z" }],
      }),
    };
    const sleep = vi.fn().mockResolvedValue(undefined);
    const logs = [];

    const result = await runNewsfeedMirror({
      src,
      dst,
      limit: 400,
      maxAttempts: 3,
      sleep,
      log: (entry) => logs.push(entry),
      now: () => "2026-07-11T12:00:00.000Z",
      runId: "run-1",
    });

    expect(result).toEqual({ mirrored: 1, total: 1 });
    expect(dst.batch).toHaveBeenCalledTimes(2);
    expect(sleep).toHaveBeenCalledTimes(1);
    expect(src.execute).toHaveBeenLastCalledWith(expect.objectContaining({
      args: expect.arrayContaining(["demo-newsfeed-mirror", "ok"]),
    }));
    expect(logs).toContainEqual(expect.objectContaining({
      run_id: "run-1",
      phase: "destination_write",
      event: "retry",
      attempt: 1,
    }));
  });

  it("bounds persistent failures and records an error heartbeat", async () => {
    const src = {
      execute: vi.fn()
        .mockResolvedValueOnce({ rows: [post()] })
        .mockResolvedValue({ rows: [] }),
    };
    const dst = {
      batch: vi.fn().mockRejectedValue(new TypeError("fetch failed")),
      execute: vi.fn(),
    };

    await expect(runNewsfeedMirror({
      src,
      dst,
      limit: 400,
      maxAttempts: 3,
      sleep: vi.fn().mockResolvedValue(undefined),
      log: vi.fn(),
      now: () => "2026-07-11T12:00:00.000Z",
      runId: "run-2",
    })).rejects.toThrow("fetch failed");

    expect(dst.batch).toHaveBeenCalledTimes(3);
    expect(src.execute).toHaveBeenLastCalledWith(expect.objectContaining({
      args: expect.arrayContaining(["demo-newsfeed-mirror", "error"]),
    }));
  });

  it("adds an abort deadline without discarding a caller signal", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(new Response("ok"));
    const caller = new AbortController();
    const boundedFetch = createBoundedFetch({ timeoutMs: 1000, fetchImpl });

    await boundedFetch("https://example.test", { signal: caller.signal });

    const init = fetchImpl.mock.calls[0][1];
    expect(init.signal).toBeInstanceOf(AbortSignal);
    caller.abort();
    expect(init.signal.aborted).toBe(true);
  });
});

describe("demo market mirror reliability", () => {
  it("classifies Turso HTTP 502 source reads as transient", () => {
    expect(isTransientTursoError(turso502())).toBe(true);
    expect(isTransientTursoError(new Error("no such table: scan_snapshots"))).toBe(false);
  });

  it("retries a transient scan_snapshots source 502 then mirrors", async () => {
    // Incident ba86fe0a: oneshot paged P1 after a single Turso 502 on the
    // scan_snapshots window read while sibling tables mirrored fine.
    const src = {
      execute: vi.fn(async (sql) => {
        const text = String(sql);
        if (text.includes("FROM scan_snapshots") && text.includes("ROW_NUMBER")) {
          if (src.execute.mock.calls.filter((c) => String(c[0]).includes("ROW_NUMBER")).length === 1) {
            throw turso502();
          }
          return { columns: ["service", "scan_time", "payload", "rn"], rows: [scanRow("gex")] };
        }
        if (text.includes("FROM gex_snapshots")) {
          return { columns: ["ticker", "scan_time", "payload", "rn"], rows: [{ ticker: "SPX", scan_time: "2026-08-21T21:00:00Z", payload: "{}" }] };
        }
        if (text.includes("ORDER BY") && text.includes("LIMIT")) {
          const table = text.match(/FROM (\w+)/)?.[1];
          return {
            columns: ["scan_time", "payload"],
            rows: [{ scan_time: "2026-08-21T21:00:00Z", payload: JSON.stringify({ table }) }],
          };
        }
        return { columns: [], rows: [] };
      }),
    };
    const dst = {
      execute: vi.fn().mockResolvedValue({ rows: [] }),
      batch: vi.fn().mockResolvedValue(undefined),
    };
    const sleep = vi.fn().mockResolvedValue(undefined);
    const logs = [];

    const result = await runMarketMirror({
      src,
      dst,
      tables: {
        latestOne: [{ table: "scanner_snapshots", orderCol: "scan_time" }],
        perKey: [{ table: "scan_snapshots", key: "service", orderCol: "scan_time" }],
        history: [],
        purgedAccountTables: [],
      },
      maxAttempts: 3,
      sleep,
      log: (entry) => logs.push(entry),
      now: () => "2026-08-21T21:45:00.000Z",
      runId: "ba86",
    });

    expect(result.total).toBeGreaterThanOrEqual(1);
    expect(result.failures).toEqual([]);
    expect(sleep).toHaveBeenCalled();
    expect(logs).toContainEqual(expect.objectContaining({
      run_id: "ba86",
      phase: "scan_snapshots:source_read",
      event: "retry",
      attempt: 1,
    }));
  });

  it("still fails the unit when scan_snapshots 502 persists past the budget", async () => {
    const src = {
      execute: vi.fn(async (sql) => {
        if (String(sql).includes("FROM scan_snapshots")) throw turso502();
        return { columns: ["scan_time", "payload"], rows: [{ scan_time: "2026-08-21T21:00:00Z", payload: "{}" }] };
      }),
    };
    const dst = {
      execute: vi.fn().mockResolvedValue({ rows: [] }),
      batch: vi.fn().mockResolvedValue(undefined),
    };

    await expect(runMarketMirror({
      src,
      dst,
      tables: {
        latestOne: [],
        perKey: [{ table: "scan_snapshots", key: "service", orderCol: "scan_time" }],
        history: [],
        purgedAccountTables: [],
      },
      maxAttempts: 3,
      sleep: vi.fn().mockResolvedValue(undefined),
      log: vi.fn(),
      now: () => "2026-08-21T21:45:00.000Z",
      runId: "ba86-fail",
    })).rejects.toThrow(/required table failures: scan_snapshots/);

    expect(src.execute).toHaveBeenCalledTimes(3);
  });

  it("does not retry non-transient SQL errors", async () => {
    const src = {
      execute: vi.fn().mockRejectedValue(new Error("no such table: scan_snapshots")),
    };
    const dst = {
      execute: vi.fn().mockResolvedValue({ rows: [] }),
      batch: vi.fn(),
    };

    await expect(runMarketMirror({
      src,
      dst,
      tables: {
        latestOne: [],
        perKey: [{ table: "scan_snapshots", key: "service", orderCol: "scan_time" }],
        history: [],
        purgedAccountTables: [],
      },
      maxAttempts: 3,
      sleep: vi.fn().mockResolvedValue(undefined),
      log: vi.fn(),
      now: () => "2026-08-21T21:45:00.000Z",
      runId: "sql-err",
    })).rejects.toThrow(/required table failures: scan_snapshots/);

    expect(src.execute).toHaveBeenCalledTimes(1);
  });

  it("purges flow_analysis_snapshots from the demo DB by default (T-108)", async () => {
    // Account-derived rows must never exist in the public demo database; the
    // recurring job is the only enforcement, so the default purge list must
    // actually issue the DELETE.
    expect(PURGED_ACCOUNT_TABLES).toEqual(["flow_analysis_snapshots"]);
    const src = { execute: vi.fn() };
    const dst = {
      execute: vi.fn().mockResolvedValue({ rows: [] }),
      batch: vi.fn().mockResolvedValue(undefined),
    };

    const result = await runMarketMirror({
      src,
      dst,
      tables: { latestOne: [], perKey: [], history: [] },
      maxAttempts: 3,
      sleep: vi.fn().mockResolvedValue(undefined),
      log: vi.fn(),
      now: () => "2026-08-23T00:00:00.000Z",
      runId: "t108-purge",
    });

    expect(result.failures).toEqual([]);
    expect(dst.execute).toHaveBeenCalledWith("DELETE FROM flow_analysis_snapshots");
    expect(src.execute).not.toHaveBeenCalled();
  });

  it("retries a transient purge 502 then completes (T-108)", async () => {
    const dst = {
      execute: vi.fn()
        .mockRejectedValueOnce(turso502())
        .mockResolvedValue({ rows: [] }),
      batch: vi.fn().mockResolvedValue(undefined),
    };
    const sleep = vi.fn().mockResolvedValue(undefined);
    const logs = [];

    const result = await runMarketMirror({
      src: { execute: vi.fn() },
      dst,
      tables: { latestOne: [], perKey: [], history: [] },
      maxAttempts: 3,
      sleep,
      log: (entry) => logs.push(entry),
      now: () => "2026-08-23T00:00:00.000Z",
      runId: "t108-retry",
    });

    expect(result.failures).toEqual([]);
    expect(dst.execute).toHaveBeenCalledTimes(2);
    expect(dst.execute).toHaveBeenNthCalledWith(2, "DELETE FROM flow_analysis_snapshots");
    expect(sleep).toHaveBeenCalledTimes(1);
    expect(logs).toContainEqual(expect.objectContaining({
      run_id: "t108-retry",
      phase: "flow_analysis_snapshots:account_purge",
      event: "retry",
      attempt: 1,
    }));
  });

  it("fails the run when the purge 502 persists past the budget (T-108)", async () => {
    // The purge used to warn "[mirror] SKIP purge" and exit 0 with
    // account-derived rows still live on demo.radon.run.
    const dst = {
      execute: vi.fn().mockRejectedValue(turso502()),
      batch: vi.fn().mockResolvedValue(undefined),
    };
    const sleep = vi.fn().mockResolvedValue(undefined);
    const logs = [];

    await expect(runMarketMirror({
      src: { execute: vi.fn() },
      dst,
      tables: { latestOne: [], perKey: [], history: [] },
      maxAttempts: 3,
      sleep,
      log: (entry) => logs.push(entry),
      now: () => "2026-08-23T00:00:00.000Z",
      runId: "t108-fail",
    })).rejects.toThrow(/account-data purge failed for flow_analysis_snapshots/);

    expect(dst.execute).toHaveBeenCalledTimes(3);
    expect(dst.execute).toHaveBeenCalledWith("DELETE FROM flow_analysis_snapshots");
    expect(sleep).toHaveBeenCalledTimes(2);
    expect(logs).toContainEqual(expect.objectContaining({
      run_id: "t108-fail",
      phase: "flow_analysis_snapshots:account_purge",
      event: "failed",
      attempt: 3,
    }));
  });
});

describe("account-data purge (REL-049 / R-097)", () => {
  /**
   * `flow_analysis_snapshots` can carry portfolio-derived flow rows and must
   * never exist in the PUBLIC demo database. The purge ran outside the retry
   * ladder and outside `failures[]`, so the documented transient 502 was
   * console.warn'd, the mirror proceeded, reported done and exited 0 —
   * production account rows left in place, indistinguishable from a clean run.
   */
  it("retries a transient 502 on the purge instead of skipping it", async () => {
    const src = {
      execute: vi.fn().mockResolvedValue({ columns: [], rows: [] }),
    };
    let purgeAttempts = 0;
    const dst = {
      execute: vi.fn(async (sql) => {
        if (String(sql).includes("DELETE FROM flow_analysis_snapshots")) {
          purgeAttempts += 1;
          if (purgeAttempts === 1) throw turso502();
        }
        return { rows: [] };
      }),
      batch: vi.fn().mockResolvedValue(undefined),
    };

    const result = await runMarketMirror({
      src,
      dst,
      tables: { latestOne: [], perKey: [], history: [], purgedAccountTables: ["flow_analysis_snapshots"] },
      maxAttempts: 3,
      sleep: vi.fn().mockResolvedValue(undefined),
      log: () => {},
      now: () => "2026-08-23T12:00:00.000Z",
      runId: "purge-retry",
    });

    expect(purgeAttempts).toBe(2);
    expect(result.failures).toEqual([]);
  });

  it("refuses to mirror at all when the purge never succeeds", async () => {
    const src = {
      execute: vi.fn().mockResolvedValue({
        columns: ["scan_time", "payload"],
        rows: [{ scan_time: "2026-08-23T12:00:00Z", payload: "{}" }],
      }),
    };
    const dst = {
      execute: vi.fn(async (sql) => {
        if (String(sql).includes("DELETE FROM flow_analysis_snapshots")) throw turso502();
        return { rows: [] };
      }),
      batch: vi.fn().mockResolvedValue(undefined),
    };

    await expect(runMarketMirror({
      src,
      dst,
      tables: {
        latestOne: [{ table: "scanner_snapshots", orderCol: "scan_time" }],
        perKey: [],
        history: [],
        purgedAccountTables: ["flow_analysis_snapshots"],
      },
      maxAttempts: 2,
      sleep: vi.fn().mockResolvedValue(undefined),
      log: () => {},
      now: () => "2026-08-23T12:00:00.000Z",
      runId: "purge-fatal",
    })).rejects.toThrow(/account-data purge failed/);

    expect(dst.batch).not.toHaveBeenCalled();
  });
});

describe("equibles per-ticker decks reach the demo database", () => {
  // Both Equibles per-ticker surfaces (13F positioning, filing-forensics
  // dossier) were absent from every mirror list, so demo.radon.run could never
  // show them for any ticker no matter what production held.
  const byTable = Object.fromEntries(PER_KEY.map((entry) => [entry.table, entry]));

  it("mirrors the newest 13F vintage per ticker on the column the route reads", () => {
    // web/app/api/equibles-smart-money-13f/route.ts orders by report_date DESC.
    expect(byTable.equibles_13f_snapshots).toEqual({
      table: "equibles_13f_snapshots",
      key: "ticker",
      orderCol: "report_date",
    });
  });

  it("mirrors the filing-forensics dossier per ticker", () => {
    expect(byTable.equibles_filing_forensics).toEqual({
      table: "equibles_filing_forensics",
      key: "ticker",
      orderCol: "as_of",
    });
  });

  it("leaves equibles_13f_holders out — nothing reads it", () => {
    // The holder rows are write-only depth: the panel renders payload.holders
    // out of the snapshot JSON, and no route or component queries the table.
    const named = [...LATEST_ONE, ...PER_KEY, ...HISTORY].map((e) => e.table);
    expect(named).not.toContain("equibles_13f_holders");
  });

  it("issues the per-key query and prune for both equibles tables", async () => {
    const src = {
      execute: vi.fn(async (sql) => {
        const table = String(sql).match(/FROM (\w+)/)?.[1];
        return { columns: ["ticker", "payload", "rn"], rows: [{ ticker: "AAPL", payload: "{}" }] };
      }),
    };
    const dst = { execute: vi.fn().mockResolvedValue({ rows: [] }), batch: vi.fn().mockResolvedValue(undefined) };

    const result = await runMarketMirror({
      src,
      dst,
      tables: {
        latestOne: [],
        perKey: [byTable.equibles_13f_snapshots, byTable.equibles_filing_forensics],
        history: [],
        purgedAccountTables: [],
      },
      maxAttempts: 1,
      sleep: vi.fn().mockResolvedValue(undefined),
      log: () => {},
      now: () => "2026-08-25T12:00:00.000Z",
      runId: "equibles",
    });

    expect(result.failures).toEqual([]);
    expect(result.total).toBe(2);
    const read = src.execute.mock.calls.map((c) => String(c[0]));
    expect(read.some((s) => /PARTITION BY ticker ORDER BY report_date DESC/.test(s))).toBe(true);
    expect(read.some((s) => /PARTITION BY ticker ORDER BY as_of DESC/.test(s))).toBe(true);
  });
});

describe("account-derived fields never reach the demo database", () => {
  // The ATS venue-share universe meta counts the open-position book
  // (universe.counts.portfolio). The demo trial tier must never learn it.
  it("strips every portfolio-derived count from the mirrored ATS venue-share payload", async () => {
    // The production shape: the portfolio tier, and the core / unique
    // aggregates that return it by subtraction from the public tiers.
    const payload = JSON.stringify({
      universe: {
        order: ["portfolio", "watchlist", "ndx100", "r2k", "sp500"],
        counts: { portfolio: 7, watchlist: 40, ndx100: 100, r2k: 0, sp500: 0, unique: 147, core: 47 },
        core: 47,
        this_cycle: 12,
        index_offset: 3,
      },
      series: {},
    });
    const src = {
      execute: vi.fn().mockResolvedValue({
        columns: ["service", "scan_time", "payload", "rn"],
        rows: [
          { service: "equibles-ats-venue-share", scan_time: "2026-08-21T21:00:00Z", payload, rn: 1 },
          { service: "gex", scan_time: "2026-08-21T21:00:00Z", payload: JSON.stringify({ counts: { portfolio: 3 } }), rn: 1 },
        ],
      }),
    };
    const dst = { execute: vi.fn().mockResolvedValue({ rows: [] }), batch: vi.fn().mockResolvedValue(undefined) };

    await runMarketMirror({
      src,
      dst,
      tables: { latestOne: [], perKey: [PER_KEY.find((e) => e.table === "scan_snapshots")], history: [], purgedAccountTables: [] },
      sleep: vi.fn().mockResolvedValue(undefined),
      log: () => {},
      runId: "strip-portfolio",
    });

    const statements = dst.batch.mock.calls[0][0].filter((s) => s.args && /^INSERT/.test(s.sql));
    const byService = Object.fromEntries(statements.map((s) => [s.args[0], JSON.parse(s.args[2])]));
    // A row the fetch did not tag cannot be classified, so it is dropped
    // whole rather than copied with only the counts removed.
    expect(byService["equibles-ats-venue-share"]).toBeUndefined();
    expect(JSON.stringify(statements)).not.toMatch(/\b(?:47|147|7)\b/);
    expect(byService.gex).toEqual({ counts: { portfolio: 3 } });
  });

  it("deletes the previously mirrored row for a service whose newest row the strip rejects", async () => {
    // The per-key prune keeps the newest DESTINATION row per service. When
    // the newest source row cannot be classified and is dropped, the row
    // mirrored before it would keep serving. The rejected key is deleted in
    // the same batch so the demo panel goes empty rather than stale.
    const src = {
      execute: vi.fn().mockResolvedValue({
        columns: ["service", "scan_time", "payload", "rn"],
        rows: [
          { service: "equibles-ats-venue-share", scan_time: "2026-08-21T21:00:00Z", payload: JSON.stringify({ tickers: ["XOFF"] }), rn: 1 },
          { service: "gex", scan_time: "2026-08-21T21:00:00Z", payload: "{}", rn: 1 },
        ],
      }),
    };
    const dst = { execute: vi.fn().mockResolvedValue({ rows: [] }), batch: vi.fn().mockResolvedValue(undefined) };

    await runMarketMirror({
      src,
      dst,
      tables: { latestOne: [], perKey: [PER_KEY.find((e) => e.table === "scan_snapshots")], history: [], purgedAccountTables: [] },
      sleep: vi.fn().mockResolvedValue(undefined),
      log: () => {},
      runId: "reject-deletes-stale",
    });

    const statements = dst.batch.mock.calls[0][0];
    const upserts = statements.filter((s) => s.args && /^INSERT/.test(s.sql));
    expect(upserts.map((s) => s.args[0])).toEqual(["gex"]);
    const deletes = statements.filter((s) => s.args && /^DELETE FROM scan_snapshots WHERE service = \?$/.test(s.sql));
    expect(deletes.map((s) => s.args)).toEqual([["equibles-ats-venue-share"]]);
    expect(JSON.stringify(statements)).not.toContain("XOFF");
  });

  it("drops an ATS venue-share row whose private-name tag is missing or malformed", () => {
    // Every open-position name is present only by the tag's say-so. Without
    // an array tag the strip cannot tell a public name from a portfolio
    // name, so the row is dropped like an unparsable one. Rows whose
    // per-ticker entries are not objects are dropped for the same reason.
    const row = (payload) => ({ service: "equibles-ats-venue-share", payload: JSON.stringify(payload) });
    const dropped = [
      { tickers: ["XOFF"], current: [{ ticker: "XOFF" }] },
      { tickers: ["XOFF"], universe: { order: ["portfolio"] } },
      { tickers: ["XOFF"], universe: { private_tickers: "XOFF", core: 1 } },
      { tickers: ["XOFF"], universe: { private_tickers: { XOFF: true } } },
      { tickers: ["XOFF"], universe: "portfolio" },
      { current: ["XOFF", null, 5], universe: { private_tickers: ["XOFF"] } },
      { errors: ["XOFF"], universe: { private_tickers: ["XOFF"] } },
      { tickers: [{ ticker: "XOFF" }], universe: { private_tickers: ["XOFF"] } },
      // series is a map. A string or array still carries the private name,
      // and a non-array carried_forward does too. Both drop whole.
      { series: "book XOFF", universe: { private_tickers: ["XOFF"], order: ["watchlist"] } },
      { series: ["XOFF", "AAPL"], universe: { private_tickers: ["XOFF"] } },
      { series: null, universe: { private_tickers: [] } },
      { carried_forward: "XOFF", universe: { private_tickers: ["XOFF"] } },
    ];
    for (const payload of dropped) {
      expect(stripAccountDerivedFields("scan_snapshots", row(payload))).toBeNull();
    }
    const kept = stripAccountDerivedFields(
      "scan_snapshots",
      row({ tickers: ["AAPL"], universe: { order: ["watchlist"], private_tickers: [] } }),
    );
    expect(JSON.parse(kept.payload)).toEqual({ tickers: ["AAPL"], universe: { order: ["watchlist"] } });
    expect(stripAccountDerivedFields("scan_snapshots", { service: "gex", payload: "{not json" })).toEqual({
      service: "gex",
      payload: "{not json",
    });
  });

  it("drops the tagged private names and the cycle position from the mirrored ATS venue-share payload", async () => {
    // A portfolio name outside every public tier reveals an open position
    // by its presence; this_cycle minus the index_offset delta reveals the
    // core size. Both are account-derived and stay in the production tier.
    const payload = JSON.stringify({
      count: 2,
      tickers: ["AAPL", "XOFF"],
      current: [{ ticker: "XOFF", z: 2 }, { ticker: "AAPL", z: 1 }],
      series: { AAPL: [{ w: 1 }], XOFF: [{ w: 1 }] },
      errors: [{ ticker: "XOFF", error: "late" }, { ticker: "MSFT", error: "404" }],
      carried_forward: ["XOFF"],
      universe: {
        order: ["portfolio", "watchlist", "ndx100", "r2k", "sp500"],
        private_tickers: ["XOFF"],
        this_cycle: 12,
        index_offset: 3,
      },
    });
    const src = {
      execute: vi.fn().mockResolvedValue({
        columns: ["service", "scan_time", "payload", "rn"],
        rows: [{ service: "equibles-ats-venue-share", scan_time: "2026-08-21T21:00:00Z", payload, rn: 1 }],
      }),
    };
    const dst = { execute: vi.fn().mockResolvedValue({ rows: [] }), batch: vi.fn().mockResolvedValue(undefined) };

    await runMarketMirror({
      src,
      dst,
      tables: { latestOne: [], perKey: [PER_KEY.find((e) => e.table === "scan_snapshots")], history: [], purgedAccountTables: [] },
      sleep: vi.fn().mockResolvedValue(undefined),
      log: () => {},
      runId: "strip-private-names",
    });

    const statements = dst.batch.mock.calls[0][0].filter((s) => s.args);
    const mirrored = JSON.parse(statements[0].args[2]);
    expect(mirrored).toEqual({
      count: 1,
      tickers: ["AAPL"],
      current: [{ ticker: "AAPL", z: 1 }],
      series: { AAPL: [{ w: 1 }] },
      errors: [{ ticker: "MSFT", error: "404" }],
      carried_forward: [],
      universe: { order: ["portfolio", "watchlist", "ndx100", "r2k", "sp500"] },
    });
    expect(statements[0].args[2]).not.toContain("XOFF");
  });
});
