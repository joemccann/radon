let meta = #{
    name: "earnings-date-service",
    description: "Ship standalone earnings date service and wire Theta Harvester (TDD)",
    when_to_use: "Build or extend the earnings-within-DTE service for scanners",
    phases: [
        #{ title: "Service", detail: "Library + pytest red/green" },
        #{ title: "Integrate", detail: "API route + theta scanner annotation" },
        #{ title: "UI", detail: "Types + Theta Harvester column" },
        #{ title: "Verify", detail: "Focused suites + review" },
    ],
};

// Optional: args.skip_ui = true to stop after backend
let skip_ui = false;
if args != () && args.skip_ui == true {
    skip_ui = true;
}

// ── Phase 1: standalone service ──────────────────────────────────
phase("Service");

let service_prompt = "";
service_prompt += "You are implementing a standalone earnings-date service in the Radon monorepo at the workspace root.\n";
service_prompt += "RULES: surgical changes only; red/green TDD; no secrets in commits; use python3.13 for pytest; match existing scripts style (fetch_informed_flow.py / fetch_catalysts.py).\n";
service_prompt += "CONTEXT: UWClient.get_earnings_by_ticker(ticker) already works. Live shape: {data:[{report_date, report_time, source, expected_move_perc, actual_eps, ...}]}.\n";
service_prompt += "Upcoming rows sort first. HONA next is 2026-08-05 postmarket (company). AAPL next is estimation.\n";
service_prompt += "fetch_catalysts.py only covers TODAY premarket/afterhours calendars — insufficient for DTE windows. Do NOT overload it; build a dedicated service.\n\n";
service_prompt += "DELIVERABLE T1:\n";
service_prompt += "1) Create scripts/earnings_dates.py with pure functions + CLI:\n";
service_prompt += "   - next_earnings(client, ticker, now=None) -> dict | None\n";
service_prompt += "   - within_dte(days_until, dte) -> bool  # True iff 0 <= days_until <= dte\n";
service_prompt += "   - annotate(client, ticker, dte=None, now=None) -> payload\n";
service_prompt += "   - batch_annotate(client, items: list[{ticker,dte?}], now=None) -> list[payload]\n";
service_prompt += "   Payload: {ticker, report_date, report_time, days_until, source, expected_move_pct, within_dte, dte, missing}\n";
service_prompt += "   report_time normalize: premarket|postmarket|unknown (map BMO/AMC/before/after if present).\n";
service_prompt += "   days_until from ET calendar day of now.\n";
service_prompt += "   Next earnings = earliest report_date >= ET today; if same-day and actual_eps already set, treat as past and take next future if any.\n";
service_prompt += "   expected_move_perc: float percent points if parseable (UW may send 0.087 = 8.7% OR already percent — if abs value < 1 treat as fraction * 100).\n";
service_prompt += "   within_dte is null when dte is None; else bool.\n";
service_prompt += "   missing=true when no upcoming earnings; still return ticker + missing.\n";
service_prompt += "   CLI: python3 scripts/earnings_dates.py TICKER [--dte N] [--json]; batch: multiple tickers.\n";
service_prompt += "   Optional disk cache data/earnings_dates/{TICKER}.json best-effort (do not cache missing-only failures from network errors).\n";
service_prompt += "2) Create scripts/tests/test_earnings_dates.py FIRST (failing), then implement until green.\n";
service_prompt += "   Cover: HONA-like same-day AMC within_dte for dte=16; past actual_eps skipped; no data missing; fraction vs percent expected_move; report_time normalization; batch; ET day boundary with fixed now.\n";
service_prompt += "3) Run: cd repo && python3.13 -m pytest scripts/tests/test_earnings_dates.py -q\n";
service_prompt += "4) Update tasks/todo.md checklist T1 complete.\n";
service_prompt += "Do not implement API/UI/theta yet. Return a short summary of files + test counts.\n";

let r1 = agent(service_prompt, #{
    label: "earnings-service",
    capability_mode: "all",
    agent_type: "general-purpose",
});

if r1 == () || !r1.success {
    complete(#{
        ok: false,
        phase: "Service",
        error: "Service agent failed",
        detail: if r1 == () { "null result" } else { r1.output },
    });
}
log("Service phase done");

// ── Phase 2: API + theta annotation (parallel) ───────────────────
phase("Integrate");

let api_prompt = "";
api_prompt += "Wire FastAPI earnings endpoint for the new scripts/earnings_dates.py service in the Radon monorepo.\n";
api_prompt += "Read scripts/earnings_dates.py and scripts/api/server.py catalysts / informed-flow patterns first.\n";
api_prompt += "DELIVERABLE T2:\n";
api_prompt += "1) GET /earnings/{ticker}?dte= optional — run_script earnings_dates.py TICKER [--dte N] --json OR import-call if already pure; prefer run_script consistency with informed-flow.\n";
api_prompt += "2) Optional GET /earnings?tickers=AAPL,HONA&dte=16 batch (cap 50 tickers).\n";
api_prompt += "3) 200 + missing flag for empty/unknown; 400 invalid ticker; never 4xx for legitimate missing earnings.\n";
api_prompt += "4) Add focused tests if server tests exist for similar routes; else a small scripts/tests or scripts/api test if pattern exists. If no API test harness is easy, document and skip API test — do not invent heavy infrastructure.\n";
api_prompt += "5) Mark T2 in tasks/todo.md. Return files changed.\n";
api_prompt += "Do not change the UI. Do not change theta scanner unless needed for import only.\n";

let theta_prompt = "";
theta_prompt += "Annotate Theta Harvester scan results with earnings-within-DTE using scripts/earnings_dates.py.\n";
theta_prompt += "Read scripts/theta_harvester_scanner.py and scripts/earnings_dates.py and scripts/tests/test_theta_harvester_scanner.py first.\n";
theta_prompt += "DELIVERABLE T3:\n";
theta_prompt += "1) After a candidate is built (or in build_output post-process), attach earnings field:\n";
theta_prompt += "   earnings: null | {report_date, report_time, days_until, within_dte, source, expected_move_pct}\n";
theta_prompt += "   Use structure.dte for within_dte. Do NOT change verdict/score gates in v1 — surface risk only.\n";
theta_prompt += "2) Prefer batch_annotate over N serial calls when scanning many tickers. Failures must not drop the theta candidate (earnings=null + optional error note).\n";
theta_prompt += "3) Extend ThetaCandidate dataclass (or attach after asdict) cleanly. Keep asdict JSON-serializable.\n";
theta_prompt += "4) Red/green tests in scripts/tests/test_theta_harvester_scanner.py for earnings annotation with mocked client.\n";
theta_prompt += "5) Run python3.13 -m pytest scripts/tests/test_theta_harvester_scanner.py scripts/tests/test_earnings_dates.py -q\n";
theta_prompt += "6) Mark T3 in tasks/todo.md. Return summary.\n";
theta_prompt += "Do not change the web UI.\n";

let integrate = parallel([
    #{ prompt: api_prompt, label: "api-route", capability_mode: "all", agent_type: "general-purpose" },
    #{ prompt: theta_prompt, label: "theta-annotate", capability_mode: "all", agent_type: "general-purpose" },
]);

let api_ok = integrate[0] != () && integrate[0].success;
let theta_ok = integrate[1] != () && integrate[1].success;
if !api_ok || !theta_ok {
    complete(#{
        ok: false,
        phase: "Integrate",
        api_ok: api_ok,
        theta_ok: theta_ok,
        api: if integrate[0] == () { () } else { integrate[0].output },
        theta: if integrate[1] == () { () } else { integrate[1].output },
    });
}
log("Integrate phase done");

if skip_ui {
    complete(#{
        ok: true,
        skipped_ui: true,
        service: r1.output,
        api: integrate[0].output,
        theta: integrate[1].output,
    });
}

// ── Phase 3: UI ──────────────────────────────────────────────────
phase("UI");

let ui_prompt = "";
ui_prompt += "Surface earnings-within-DTE on the Theta Harvester scanner UI in Radon web/.\n";
ui_prompt += "Read web/components/ThetaHarvesterScanner.tsx, web/lib/types.ts ThetaHarvester* types, web/tests/theta-harvester-scanner.test.tsx, brand rules in CLAUDE.md (no raw hex, tokens, no em dashes in user copy).\n";
ui_prompt += "Backend now attaches row.earnings: null | {report_date, report_time, days_until, within_dte, source, expected_move_pct}.\n";
ui_prompt += "DELIVERABLE T4:\n";
ui_prompt += "1) Extend ThetaHarvesterResult type with optional earnings field.\n";
ui_prompt += "2) Add Earnings column (desktop table) + mobile card line.\n";
ui_prompt += "   Display rules:\n";
ui_prompt += "   - missing/null: ---\n";
ui_prompt += "   - days_until===0 && postmarket: TODAY AMC\n";
ui_prompt += "   - days_until===0 && premarket: TODAY BMO\n";
ui_prompt += "   - else short date + BMO/AMC/TBD (e.g. Aug 5 AMC)\n";
ui_prompt += "   - if within_dte true: warning tone / pill (use existing danger/warn token classes already in scanner CSS; do not invent gradients)\n";
ui_prompt += "3) Header tooltip: Next earnings date if it falls inside the short-strangle DTE window. AMC=after close, BMO=before open.\n";
ui_prompt += "4) Vitest coverage for formatting + within_dte styling in web/tests/theta-harvester-scanner.test.tsx (and types if needed).\n";
ui_prompt += "5) Run focused vitest from web/: npx vitest run tests/theta-harvester-scanner.test.tsx\n";
ui_prompt += "6) Mark T4 in tasks/todo.md. Return summary.\n";
ui_prompt += "Do not commit or push.\n";

let r3 = agent(ui_prompt, #{
    label: "theta-ui",
    capability_mode: "all",
    agent_type: "general-purpose",
});

if r3 == () || !r3.success {
    complete(#{
        ok: false,
        phase: "UI",
        error: "UI agent failed",
        detail: if r3 == () { "null result" } else { r3.output },
    });
}
log("UI phase done");

// ── Phase 4: verify ──────────────────────────────────────────────
phase("Verify");

let verify_prompt = "";
verify_prompt += "Verify the earnings date service + Theta Harvester integration in the Radon monorepo. Read-only first, then run tests; fix only clear breakages you introduced.\n";
verify_prompt += "1) Confirm files exist: scripts/earnings_dates.py, scripts/tests/test_earnings_dates.py, theta scanner earnings field, FastAPI /earnings route, web types + ThetaHarvesterScanner earnings column.\n";
verify_prompt += "2) Run: python3.13 -m pytest scripts/tests/test_earnings_dates.py scripts/tests/test_theta_harvester_scanner.py scripts/tests/test_fetch_catalysts.py -q\n";
verify_prompt += "3) From web/: npx vitest run tests/theta-harvester-scanner.test.tsx tests/theta-harvester-route.test.ts\n";
verify_prompt += "4) Optional smoke: python3 scripts/earnings_dates.py HONA --dte 16 --json (needs network/UW; if fails for auth, note it).\n";
verify_prompt += "5) Update tasks/todo.md T5 + Review section with evidence (commands + pass counts). Do not commit/push.\n";
verify_prompt += "Return: pass/fail tables, file list, any residual gaps.\n";

let r4 = agent(verify_prompt, #{
    label: "verify",
    capability_mode: "all",
    agent_type: "general-purpose",
});

if r4 == () || !r4.success {
    complete(#{
        ok: false,
        phase: "Verify",
        error: "Verify agent failed",
        detail: if r4 == () { "null result" } else { r4.output },
        prior: #{ service: r1.output, api: integrate[0].output, theta: integrate[1].output, ui: r3.output },
    });
}

let report = "";
report += "# Earnings date service — workflow result\n\n";
report += "## Service\n" + r1.output + "\n\n";
report += "## API\n" + integrate[0].output + "\n\n";
report += "## Theta annotation\n" + integrate[1].output + "\n\n";
report += "## UI\n" + r3.output + "\n\n";
report += "## Verify\n" + r4.output + "\n";

let path = write_scratch_file("earnings-date-service-report.md", report);
complete(#{
    ok: true,
    path: path,
    summary: "Standalone earnings service + Theta Harvester wiring shipped; see report",
});
