let meta = #{
    name: "cut-uw-daily-quota",
    description: "Serial TDD implementation of the UW 40k daily-quota cut plan",
    when_to_use: "Execute the UW quota plan one verified step at a time",
    phases: [
        #{ title: "Cadence", detail: "60 min timer + 3600s FastAPI cache" },
        #{ title: "NoRetry", detail: "do not retry daily-cap 429" },
        #{ title: "Budget", detail: "refuse NDX scans after 50 percent" },
        #{ title: "SharedFetch", detail: "one surface fetch for theta and strength" },
        #{ title: "SlimStrength", detail: "fewer live UW calls, same 7 groups" },
        #{ title: "DiskCache", detail: "disk TTL cache in UWClient" },
        #{ title: "TickerCache", detail: "ticker-info stock-state TTL" },
        #{ title: "IbFirst", detail: "IB daily bars before UW OHLC" },
        #{ title: "UsageApi", detail: "GET /uw/usage + launchd note" },
        #{ title: "Verify", detail: "focused suites for every touched file" },
    ],
};

let step_schema = #{
    "type": "object",
    "required": ["ok", "step", "tests", "files", "notes"],
    "properties": #{
        "ok": #{ "type": "boolean" },
        "step": #{ "type": "string" },
        "tests": #{ "type": "string" },
        "files": #{ "type": "array", "items": #{ "type": "string" } },
        "notes": #{ "type": "string" },
    },
};

fn rules() {
    let s = "";
    s += "You are in the Radon repo at the workspace root. Red/green TDD. Surgical. No commits. No push.\n";
    s += "Ignore unrelated dirty files (.deepsec, other html, implement-stop-orders.rhai, lessons.md except if you must).\n";
    s += "Use rg and read_file first. Confirm pwd before pytest/vitest.\n";
    s += "Python: python3.13 scripts/run_pytest_affected.py --files <touched> -- -q\n";
    s += "If that helper is awkward, run: cd scripts && python3.13 -m pytest tests/<file> -q\n";
    s += "JS: from web/, npx vitest run <testfile> \n";
    s += "No em dashes in user-facing copy. No raw hex in UI. Do not refactor adjacent code.\n";
    s += "If tests stay red, stop and return ok=false with the failing node id. Do not leave half-broken scanners.\n";
    s += "Ticker scans must still bypass preset cooldown. Empty NDX snapshots must still refuse to clobber last good cache.\n";
    return s;
}

fn halt(step, result) {
    complete(#{
        ok: false,
        failed_step: step,
        tests: result.tests,
        notes: result.notes,
        files: result.files,
    });
}

// ── U1 Cadence ──────────────────────────────────────────────
phase("Cadence");
let p1 = "";
p1 += rules();
p1 += "STEP U1: hourly signals refresh + 3600s FastAPI cache window.\n";
p1 += "WRITE FAILING TESTS FIRST, then implement.\n";
p1 += "1) cloud/tests/test_systemd_services.py: assert radon-signals-refresh.timer OnCalendar is hourly at minute 00 ET (09..16:00:00 America/New_York), Mon-Fri. Must NOT contain :00,15,30,45. Keep America/New_York. Persistent=false.\n";
p1 += "2) scripts/api/tests: THETA_COOLDOWN_S and STRENGTH_COOLDOWN_S equal 3600. Existing ticker-bypass tests must stay green. Add or extend a preset-scan test that a second POST inside 3600s returns cache and does not call run_script.\n";
p1 += "IMPLEMENT: cloud/services/radon-signals-refresh.timer comments + OnCalendar. scripts/api/server.py THETA_COOLDOWN_S and STRENGTH_COOLDOWN_S = 3600. Update scripts/api/CLAUDE.md timer table row for radon-signals-refresh to hourly ET.\n";
p1 += "Do not change run_signals_refresh.sh scan order. Return {ok, step:U1, tests, files, notes}.\n";
let r1 = agent(p1, #{ label: "u1-cadence", capability_mode: "all", output_schema: step_schema });
if r1 == () || !r1.success || r1.output.ok != true { halt("U1", if r1 != () && r1.success { r1.output } else { #{ ok: false, step: "U1", tests: "", files: [], notes: "agent failed" } }); }
log("U1 cadence done");

// ── U2 No daily-cap retry ───────────────────────────────────
phase("NoRetry");
let p2 = "";
p2 += rules();
p2 += "STEP U2: UWClient must not retry a 429 whose body/message contains daily request limit.\n";
p2 += "Existing test_retry_on_429 (transient 429 then 200) MUST remain green.\n";
p2 += "WRITE FAILING TEST in scripts/tests/test_uw_client.py: 429 with json {message: 'daily request limit of 40000'} raises UWRateLimitError and session.get.call_count == 1 (no sleep retry).\n";
p2 += "IMPLEMENT in scripts/clients/uw_client.py _get: if status==429 and 'daily request limit' in msg.lower(): raise immediately.\n";
p2 += "Reuse the marker string already used in scripts/fetch_skew.py if practical. Return {ok, step:U2, tests, files, notes}.\n";
let r2 = agent(p2, #{ label: "u2-noretry", capability_mode: "all", output_schema: step_schema });
if r2 == () || !r2.success || r2.output.ok != true { halt("U2", if r2 != () && r2.success { r2.output } else { #{ ok: false, step: "U2", tests: "", files: [], notes: "agent failed" } }); }
log("U2 no-retry done");

// ── U3 Budget ───────────────────────────────────────────────
phase("Budget");
let p3 = "";
p3 += rules();
p3 += "STEP U3: process-wide UW daily budget. NDX universe scans refuse after 50 percent of 40000.\n";
p3 += "Create scripts/utils/uw_budget.py (and scripts/tests/test_uw_budget.py):\n";
p3 += "- Quota day is ET, reset at 20:00 ET (same as fetch_skew).\n";
p3 += "- record_hit() increments a count persisted to data/uw_budget.json (date + count). Tests must inject a temp path.\n";
p3 += "- used(), remaining(), should_block_universe_scan() true when used >= 20000.\n";
p3 += "- Per-ticker / explicit ticker scans are NOT blocked.\n";
p3 += "Wire UWClient._get to record_hit() only on a real HTTP attempt (not in-memory cache hit).\n";
p3 += "Wire theta_harvester_scanner.scan_universe and strength_confirmation_scanner.scan_universe: if should_block_universe_scan and no explicit ticker list, skip workers, do not persist empty clobber, return last good cache shape or a degraded coverage block. FastAPI preset routes should then serve last good file.\n";
p3 += "Do not block evaluate.py or fetch_flow. Return {ok, step:U3, tests, files, notes}.\n";
let r3 = agent(p3, #{ label: "u3-budget", capability_mode: "all", output_schema: step_schema });
if r3 == () || !r3.success || r3.output.ok != true { halt("U3", if r3 != () && r3.success { r3.output } else { #{ ok: false, step: "U3", tests: "", files: [], notes: "agent failed" } }); }
log("U3 budget done");

// ── U4 Shared fetch ─────────────────────────────────────────
phase("SharedFetch");
let p4 = "";
p4 += rules();
p4 += "STEP U4: shared UW surface fetch used by both scanners.\n";
p4 += "Add scripts/utils/uw_surface.py: fetch_surface(client, ticker) returns a dict with ohlc, iv_rank, contracts, gex_strike (raw UW payloads). One function, four GETs.\n";
p4 += "Change scripts/theta_harvester_scanner.py scan_ticker and scripts/strength_confirmation_scanner.py scan_ticker to call fetch_surface instead of those four client methods directly.\n";
p4 += "Keep existing scoring. Tests: new scripts/tests/test_uw_surface.py plus update scanner tests so a mock surface is enough for those four fields.\n";
p4 += "Do not drop strength's other fetches yet (that is U5). Return {ok, step:U4, tests, files, notes}.\n";
let r4 = agent(p4, #{ label: "u4-surface", capability_mode: "all", output_schema: step_schema });
if r4 == () || !r4.success || r4.output.ok != true { halt("U4", if r4 != () && r4.success { r4.output } else { #{ ok: false, step: "U4", tests: "", files: [], notes: "agent failed" } }); }
log("U4 shared fetch done");

// ── U5 Slim strength ────────────────────────────────────────
phase("SlimStrength");
let p5 = "";
p5 += rules();
p5 += "STEP U5: cut live UW calls in strength scan_ticker. Keep the 7-group scoring API and verdict_for().\n";
p5 += "STOP calling live: get_stock_flow_alerts, get_options_volume, get_greek_exposure (aggregate), get_volatility_term_structure, get_historical_risk_reversal_skew.\n";
p5 += "KEEP live via fetch_surface: ohlc, iv_rank, contracts, gex_strike. KEEP get_stock_oi_change (CALL POSITIONING). KEEP build_market_context once per universe (sector ETFs + CRI/breadth disk).\n";
p5 += "Degrade dropped sources: pass empty payloads so existing analyze_* functions still run; do not delete the 7 groups.\n";
p5 += "Update scripts/tests/test_strength_confirmation.py (or equivalent) so they do not require those five live methods. Return {ok, step:U5, tests, files, notes}.\n";
let r5 = agent(p5, #{ label: "u5-slim", capability_mode: "all", output_schema: step_schema });
if r5 == () || !r5.success || r5.output.ok != true { halt("U5", if r5 != () && r5.success { r5.output } else { #{ ok: false, step: "U5", tests: "", files: [], notes: "agent failed" } }); }
log("U5 slim strength done");

// ── U6 Disk cache ───────────────────────────────────────────
phase("DiskCache");
let p6 = "";
p6 += rules();
p6 += "STEP U6: persist UW GET responses on disk with endpoint-class TTLs. In-memory 60s cache stays.\n";
p6 += "TTLs: ohlc/iv_rank/gex/contracts 15 min; stock info 60 min; flow/alerts 2 min; default 60s.\n";
p6 += "Put files under data/uw_http_cache/ (gitignored if needed). Never cache 4xx/5xx. Tests use a temp dir.\n";
p6 += "Wire into UWClient._get after memory cache miss, before HTTP. record_hit only on HTTP.\n";
p6 += "Tests in scripts/tests/test_uw_cache.py or test_uw_client.py. Return {ok, step:U6, tests, files, notes}.\n";
let r6 = agent(p6, #{ label: "u6-disk", capability_mode: "all", output_schema: step_schema });
if r6 == () || !r6.success || r6.output.ok != true { halt("U6", if r6 != () && r6.success { r6.output } else { #{ ok: false, step: "U6", tests: "", files: [], notes: "agent failed" } }); }
log("U6 disk cache done");

// ── U7 Ticker cache ─────────────────────────────────────────
phase("TickerCache");
let p7 = "";
p7 += rules();
p7 += "STEP U7: ticker info HIT path currently refreshes UW stock-state on every request. Add a 15-minute stock-state TTL so a HIT does not call UW until that TTL expires.\n";
p7 += "File: web/app/api/ticker/info/route.ts. Keep uw_info populated-cache contract (do not serve empty uw_info).\n";
p7 += "previous-close already day-caches; add a test that a second POST for the same symbol does not call unusualwhales if the in-memory cache is warm. Do not weaken IB-first.\n";
p7 += "Vitest in web/tests. Return {ok, step:U7, tests, files, notes}.\n";
let r7 = agent(p7, #{ label: "u7-ticker", capability_mode: "all", output_schema: step_schema });
if r7 == () || !r7.success || r7.output.ok != true { halt("U7", if r7 != () && r7.success { r7.output } else { #{ ok: false, step: "U7", tests: "", files: [], notes: "agent failed" } }); }
log("U7 ticker cache done");

// ── U8 IB-first ─────────────────────────────────────────────
phase("IbFirst");
let p8 = "";
p8 += rules();
p8 += "STEP U8: IB-first daily bars inside the shared surface/OHLC path.\n";
p8 += "fetch_surface (or a helper it calls) tries IB historical daily closes first; UW get_stock_ohlc only if IB returns too few bars or throws. Never raise out of IB into the scanner; fall through to UW.\n";
p8 += "Do not add a new IB connection storm: reuse existing bounded IB helpers if one exists (scripts/clients or fetch_options). If wiring IB is unsafe without a live gateway, implement a seam fetch_daily_closes(ticker, ib=..., uw=...) that tests can pin, and call it from fetch_surface.\n";
p8 += "Tests: IB hit skips UW ohlc; IB miss calls UW. Return {ok, step:U8, tests, files, notes}.\n";
let r8 = agent(p8, #{ label: "u8-ib", capability_mode: "all", output_schema: step_schema });
if r8 == () || !r8.success || r8.output.ok != true { halt("U8", if r8 != () && r8.success { r8.output } else { #{ ok: false, step: "U8", tests: "", files: [], notes: "agent failed" } }); }
log("U8 IB-first done");

// ── U9 Usage API ────────────────────────────────────────────
phase("UsageApi");
let p9 = "";
p9 += rules();
p9 += "STEP U9: GET /uw/usage on FastAPI returning {used, limit:40000, remaining, reset_et:'20:00', quota_day, universe_scans_blocked}. Read from uw_budget. Auth like other operator GETs (trusted local / clerk).\n";
p9 += "Test in scripts/api/tests. Also add a short comment at the top of config/com.radon.data-refresh.plist: must stay unloaded while VPS owns signals or laptop scanner+discover will double UW burn. One sentence in docs/operations.md if that file lists the plist.\n";
p9 += "No new dashboard page. No buy-more-volume work. Return {ok, step:U9, tests, files, notes}.\n";
let r9 = agent(p9, #{ label: "u9-usage", capability_mode: "all", output_schema: step_schema });
if r9 == () || !r9.success || r9.output.ok != true { halt("U9", if r9 != () && r9.success { r9.output } else { #{ ok: false, step: "U9", tests: "", files: [], notes: "agent failed" } }); }
log("U9 usage API done");

// ── Final verify ────────────────────────────────────────────
phase("Verify");
let pv = "";
pv += rules();
pv += "FINAL VERIFY. Do not add features. Run focused tests for every file this workflow touched:\n";
pv += "cloud/tests/test_systemd_services.py (signals timer cases)\n";
pv += "scripts/tests/test_uw_client.py scripts/tests/test_uw_budget.py scripts/tests/test_uw_surface.py\n";
pv += "scripts/tests/test_theta_harvester.py scripts/tests/test_strength_confirmation.py (actual names via rg)\n";
pv += "scripts/api/tests/test_theta_harvester_route.py scripts/api/tests/test_strength_confirmation_route.py if present\n";
pv += "plus any new test files.\n";
pv += "web vitest for ticker-info / previous-close if those tests exist.\n";
pv += "If anything is red, fix only regressions from this plan, re-run, then return ok.\n";
pv += "Mark U1-U9 complete in tasks/todo.md checkboxes. Return {ok, step:VERIFY, tests, files, notes}.\n";
let rv = agent(pv, #{ label: "verify", capability_mode: "all", output_schema: step_schema });
if rv == () || !rv.success || rv.output.ok != true { halt("VERIFY", if rv != () && rv.success { rv.output } else { #{ ok: false, step: "VERIFY", tests: "", files: [], notes: "agent failed" } }); }

let report = "";
report += "UW quota cut serial run complete.\n";
report += json_encode(#{
    u1: r1.output,
    u2: r2.output,
    u3: r3.output,
    u4: r4.output,
    u5: r5.output,
    u6: r6.output,
    u7: r7.output,
    u8: r8.output,
    u9: r9.output,
    verify: rv.output,
});
let path = write_scratch_file("uw-quota-cut.md", report);
complete(#{ ok: true, path: path, verify: rv.output.tests });
