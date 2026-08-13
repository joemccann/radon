let meta = #{
    name: "expand-leap-garch-universes",
    description: "Default LEAP and GARCH scanners to Nasdaq-100 + S&P 500 + Russell 2000",
    when_to_use: "Widen LEAP IV and GARCH convergence scans to the three US index universes",
    phases: [
        #{ title: "Tests", detail: "Red tests for virtual indexes preset, pairing, timeouts" },
        #{ title: "Backend", detail: "Resolver, workers, FastAPI, refresh wrappers" },
        #{ title: "Web", detail: "SCAN defaults, Next timeouts, systemd budgets" },
        #{ title: "Verify", detail: "Focused pytest + vitest + cloud unit tests" },
    ],
};

let summary_schema = #{
    "type": "object",
    "required": ["ok", "files", "notes"],
    "properties": #{
        "ok": #{ "type": "boolean" },
        "files": #{ "type": "array", "items": #{ "type": "string" } },
        "notes": #{ "type": "string" },
        "tests": #{ "type": "string" },
    },
};

// Canned validate_only output may omit ok; only abort when an agent
// explicitly returns ok=false.
fn abort_if_failed(r, phase_name) {
    if r == () || !r.success {
        complete(#{ ok: false, phase: phase_name, error: "agent failed" });
    }
    if r.output != () && r.output.ok == false {
        complete(#{
            ok: false,
            phase: phase_name,
            notes: r.output.notes,
            files: r.output.files,
        });
    }
}

// ── Phase 1: red tests ──────────────────────────────────────────
phase("Tests");

let tests_prompt = "";
tests_prompt += "You are in the Radon repo at the workspace root. Red/green TDD. Surgical. No commits. No live UW/IB calls.\n";
tests_prompt += "Do NOT implement production code in this phase. Write FAILING tests only.\n";
tests_prompt += "Ignore unrelated dirty files (.deepsec, vol-cone fixtures, x-list-feed-plan, implement-stop-orders.rhai).\n\n";
tests_prompt += "GOAL: LEAP and GARCH default universes become Nasdaq-100 + S&P 500 + Russell 2000 so scheduled/dashboard scans produce real results (~2494 unique names, ~1295 curated pairs).\n\n";
tests_prompt += "CONTRACT:\n";
tests_prompt += "1) Virtual preset name `indexes` (do NOT commit a giant indexes.json). load_preset('indexes') unions data/presets/{ndx100,sp500,r2k}.json.\n";
tests_prompt += "   Tickers: order-preserving unique union. Pairs: unique by unordered {a,b}. Description mentions all three indexes.\n";
tests_prompt += "   Existing slugs ndx100, sp500, r2k stay file-backed. mag7 / mega-tech / all (built-in GARCH pair packs) stay unchanged.\n";
tests_prompt += "   Live counts today: ndx100 103/47, sp500 503/275, r2k 1976/990, union 2494/1295. Assert floors: ndx100>=80 tickers + >=20 pairs; sp500>=400 + >=200; r2k>=1200 + >=500; indexes>=2000 tickers + >=700 pairs; NVDA and AAPL in indexes; indexes tickers == unique(ndx+spx+r2k).\n";
tests_prompt += "2) GARCH resolve_inputs(None, 'indexes') MUST use those curated pairs. Never consecutive-pair 2500 names. resolve_inputs still consecutive-pairs explicit ticker lists.\n";
tests_prompt += "3) LEAP --preset indexes resolves via load_preset (file-preset branch). Add --workers (default 16). Scan loop must be parallel (ThreadPoolExecutor), skip UWRateLimitError, quiet per-ticker prints at scale.\n";
tests_prompt += "4) Defaults become indexes: FastAPI /leap/scan and /garch-convergence/scan omitted preset; run_leap_refresh.sh RADON_LEAP_REFRESH_PRESET; run_garch_refresh.sh RADON_GARCH_REFRESH_PRESET; web WorkspaceSections SCAN body.\n";
tests_prompt += "5) Timeouts for INDEX-SCALE preset scans (ticker scans stay short):\n";
tests_prompt += "   FastAPI leap ticker 300s, leap preset 3600s; garch ticker 180s, garch preset 3600s.\n";
tests_prompt += "   Next.js radonFetch 3_610_000 ms on both scan routes (max wait; ticker scans still finish fast).\n";
tests_prompt += "   Refresh curl -m 3610. radon-leap.service and radon-garch.service TimeoutStartSec>=3900.\n";
tests_prompt += "   FastAPI passes --workers from RADON_LEAP_SCANNER_WORKERS / RADON_GARCH_SCANNER_WORKERS via _bounded_env_int (default 16).\n\n";
tests_prompt += "WRITE/EXTEND:\n";
tests_prompt += "A) scripts/tests/test_index_universe_preset.py (new): load_preset indexes + floors + uniqueness + traversal still FileNotFoundError for '../indexes'.\n";
tests_prompt += "B) scripts/tests/test_garch_convergence_scanner.py: resolve_inputs(None,'indexes') pair count >=700 and every pair is a 2-list; explicit consecutive pairing unchanged.\n";
tests_prompt += "C) scripts/tests/test_leap_scanner.py: resolve helper / universe stamp for preset:indexes; workers argparse if you extract a resolve function. Do not import-time fetch.\n";
tests_prompt += "D) scripts/api/tests/test_leap_route.py: omitted preset forwards --preset indexes and timeout=3600; explicit ticker path still timeout=300; ?preset=mag7 still works with timeout=3600 (preset path).\n";
tests_prompt += "E) scripts/api/tests/test_garch_convergence_route.py: omitted default is indexes timeout=3600; ticker scan still 180; ?preset=mega-tech still accepted timeout=3600. Cooldown cache universe preset:indexes for omitted-preset cooldown test.\n";
tests_prompt += "F) web/tests/leap-garch-scan-route.test.ts: radonFetch timeout 3610000 (keep mag7/mega-tech forwarding cases).\n";
tests_prompt += "G) web/tests: assert WorkspaceSections SCAN posts {preset:'indexes'} for LEAP and GARCH when no custom tickers. Grep existing WorkspaceSections tests and extend; if none, add a focused test that mocks fetch.\n";
tests_prompt += "H) cloud/tests/test_systemd_services.py: TestLeapGarchScanBudget like TestBpiScanBudget — TimeoutStartSec >= 3900 on radon-leap.service and radon-garch.service.\n";
tests_prompt += "I) Optional: grep run_leap_refresh.sh / run_garch_refresh.sh in a small python test or just cover via backend phase. Prefer a tiny scripts/tests/test_leap_garch_refresh_defaults.py that reads the shell files and asserts default preset=indexes and curl -m 3610.\n\n";
tests_prompt += "Prepend a task block to tasks/todo.md (do not wipe other tasks) with T1 tests / T2 backend / T3 web+systemd / T4 verify.\n";
tests_prompt += "Run the NEW/updated unit tests and confirm they FAIL for the right reason (missing indexes preset / old defaults / old timeouts). Do not implement the feature.\n";
tests_prompt += "Return {ok, files, notes, tests}.\n";

let r_tests = agent(tests_prompt, #{
    label: "red-tests",
    capability_mode: "all",
    agent_type: "general-purpose",
    output_schema: summary_schema,
});
abort_if_failed(r_tests, "Tests");
log("T1 red tests written");

// ── Phase 2: backend ────────────────────────────────────────────
phase("Backend");

let backend_prompt = "";
backend_prompt += "You are in the Radon repo. Make the index-universe scanners green. Surgical. No commits. Implement only what the failing tests require plus the contract below.\n";
backend_prompt += "Do not change Next.js UI components or systemd unit files in this phase (except if a python test reads the refresh shell scripts).\n\n";
backend_prompt += "IMPLEMENT:\n";
backend_prompt += "1) scripts/utils/presets.py\n";
backend_prompt += "   INDEX_UNIVERSE_PARTS = ['ndx100','sp500','r2k']\n";
backend_prompt += "   load_preset('indexes') unions those three file presets. Dedupe tickers order-preserving. Dedupe pairs by frozenset of the two symbols (keep first order).\n";
backend_prompt += "   list_presets() includes indexes with the union ticker count (virtual; no indexes.json).\n";
backend_prompt += "   Traversal guard unchanged. Do not bypass _PRESET_NAME_RE.\n";
backend_prompt += "2) scripts/leap_scanner_uw.py\n";
backend_prompt += "   --preset indexes already works once load_preset does. Add --workers default 16.\n";
backend_prompt += "   Parallelize scan_ticker with ThreadPoolExecutor + one shared UWClient (mirror garch_convergence.fetch_all_tickers).\n";
backend_prompt += "   Skip ticker on UWRateLimitError (import from clients.uw_client if that type exists; else catch and continue).\n";
backend_prompt += "   Do not print every ticker name when len(tickers)>20; print count + universe.\n";
backend_prompt += "   Quiet per-ticker banners at workers>1 (errors + mispriced only).\n";
backend_prompt += "3) scripts/garch_convergence.py\n";
backend_prompt += "   resolve_inputs already uses load_preset pairs. Confirm --preset indexes yields curated pairs, not consecutive pairing.\n";
backend_prompt += "   Default --workers 16. Help text mentions indexes = NDX+SPX+RUT curated pairs.\n";
backend_prompt += "   Do not change meaning of --preset all (still the four tiny built-in packs).\n";
backend_prompt += "4) scripts/api/server.py\n";
backend_prompt += "   leap_scan preset default 'indexes'. garch_convergence_scan preset default 'indexes'.\n";
backend_prompt += "   LEAP_PRESET_TIMEOUT_S=3600, LEAP_TICKER_TIMEOUT_S=300.\n";
backend_prompt += "   GARCH_PRESET_TIMEOUT_S=3600, GARCH_TICKER_TIMEOUT_S=180.\n";
backend_prompt += "   Pass --workers from _bounded_env_int('RADON_LEAP_SCANNER_WORKERS', 16) and RADON_GARCH_SCANNER_WORKERS default 16.\n";
backend_prompt += "   Explicit ticker scans keep short timeouts and do not need workers unless already cheap.\n";
backend_prompt += "5) scripts/run_leap_refresh.sh and scripts/run_garch_refresh.sh\n";
backend_prompt += "   Default preset indexes. curl -m 3610. Comments updated. FastAPI URL still uses the preset query param.\n";
backend_prompt += "6) Docs that hardcode mag7/mega-tech as THE scheduled default: update only the scheduled-default lines in docs/scripts-reference.md and the scanner module docstrings. Do not rewrite strategy specs.\n\n";
backend_prompt += "RUN:\n";
backend_prompt += "python3.13 -m pytest scripts/tests/test_index_universe_preset.py scripts/tests/test_garch_convergence_scanner.py scripts/tests/test_leap_scanner.py scripts/tests/test_presets_traversal.py scripts/tests/test_leap_garch_refresh_defaults.py scripts/api/tests/test_leap_route.py scripts/api/tests/test_garch_convergence_route.py -q\n";
backend_prompt += "Fix until those are green. Mark T2 in tasks/todo.md. Return {ok, files, notes, tests}.\n";

let r_backend = agent(backend_prompt, #{
    label: "backend",
    capability_mode: "all",
    agent_type: "general-purpose",
    output_schema: summary_schema,
});
abort_if_failed(r_backend, "Backend");
log("T2 backend green");

// ── Phase 3: web + systemd ──────────────────────────────────────
phase("Web");

let web_prompt = "";
web_prompt += "You are in the Radon repo. Ship dashboard + timeout + systemd budget for the indexes universe. Surgical. No commits. Brand: tokens only, no em dashes, no emoji in user copy.\n\n";
web_prompt += "IMPLEMENT:\n";
web_prompt += "1) web/components/WorkspaceSections.tsx runLeapScan / runGarchScan: when no custom tickers, POST {preset:'indexes'} (was mag7 / mega-tech).\n";
web_prompt += "2) web/app/api/leap/scan/route.ts and web/app/api/garch-convergence/scan/route.ts: radonFetch timeout 3_610_000. Update comments (FastAPI default preset is now indexes).\n";
web_prompt += "3) cloud/services/radon-leap.service and cloud/services/radon-garch.service: TimeoutStartSec=3900. Add a one-line comment that the indexes universe is ~2500 names / ~1300 pairs.\n";
web_prompt += "4) Empty-state copy in LeapScanner / GarchConvergenceScanner may mention the scheduled indexes refresh if it currently says only mag7/mega-tech. Keep cadence derived, no hardcoded 'Refreshes 5m'.\n";
web_prompt += "5) Do not add a preset picker unless tests require it. SCAN = full three-index universe.\n\n";
web_prompt += "RUN from repo:\n";
web_prompt += "cd web && npx vitest run tests/leap-garch-scan-route.test.ts tests/leap-garch-scanner.test.tsx --reporter=dot\n";
web_prompt += "Also run any new WorkspaceSections test file you added.\n";
web_prompt += "python3.13 -m pytest cloud/tests/test_systemd_services.py -q -k 'LeapGarch or leap or garch or BpiScan'\n";
web_prompt += "Fix until green. Mark T3 in tasks/todo.md. Return {ok, files, notes, tests}.\n";

let r_web = agent(web_prompt, #{
    label: "web-systemd",
    capability_mode: "all",
    agent_type: "general-purpose",
    output_schema: summary_schema,
});
abort_if_failed(r_web, "Web");
log("T3 web and systemd green");

// ── Phase 4: verify ─────────────────────────────────────────────
phase("Verify");

let verify_prompt = "";
verify_prompt += "You are in the Radon repo. Verify the indexes-universe LEAP/GARCH change. No new features. No commits.\n";
verify_prompt += "Re-run the focused suites and fix any breakage you caused.\n\n";
verify_prompt += "RUN:\n";
verify_prompt += "1) python3.13 -m pytest scripts/tests/test_index_universe_preset.py scripts/tests/test_garch_convergence_scanner.py scripts/tests/test_leap_scanner.py scripts/tests/test_presets_traversal.py scripts/tests/test_leap_garch_refresh_defaults.py scripts/api/tests/test_leap_route.py scripts/api/tests/test_garch_convergence_route.py cloud/tests/test_systemd_services.py -q\n";
verify_prompt += "2) cd web && npx vitest run tests/leap-garch-scan-route.test.ts tests/leap-garch-scanner.test.tsx --reporter=dot  (plus any new WorkspaceSections test)\n";
verify_prompt += "3) Confirm with rg that defaults are indexes: run_leap_refresh.sh, run_garch_refresh.sh, server.py leap_scan/garch_convergence_scan, WorkspaceSections.tsx.\n";
verify_prompt += "4) Confirm load_preset('indexes') ticker count >= 2000 via a one-liner python3.13 (no network).\n";
verify_prompt += "5) Do NOT run the live 2494-ticker UW scan.\n";
verify_prompt += "Mark T4 in tasks/todo.md and add a short Review section. Return {ok, files, notes, tests}.\n";

let r_verify = agent(verify_prompt, #{
    label: "verify",
    capability_mode: "all",
    agent_type: "general-purpose",
    output_schema: summary_schema,
});
abort_if_failed(r_verify, "Verify");
log("T4 verified");

complete(#{
    ok: true,
    summary: "LEAP and GARCH default to indexes (NDX+SPX+RUT)",
    tests: r_verify.output.tests,
    files: r_verify.output.files,
    notes: r_verify.output.notes,
});
