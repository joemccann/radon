let meta = #{
    name: "fix-bpi-post-close-stale",
    description: "After the ET close, BPI must show that session or an honest STALE mark",
    when_to_use: "Regime BPI as-of is yesterday after the cash close",
    phases: [
        #{ title: "Tests", detail: "Red tests for post-close session currency" },
        #{ title: "Fix", detail: "Evening catch-up, spark last-bar, STALE UI" },
        #{ title: "Verify", detail: "pytest + vitest + Playwright" },
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

phase("Tests");

let tests_prompt = "";
tests_prompt += "You are in the Radon repo at the workspace root. Red/green TDD. Surgical. No commits.\n";
tests_prompt += "Do NOT implement production code. Write FAILING tests only.\n";
tests_prompt += "Ignore unrelated dirty files and the in-flight proposal-card workflow.\n";
tests_prompt += "Use rg / read_file first. Confirm pwd before pytest/vitest.\n\n";
tests_prompt += "BUG (production screenshot 2026-08-13, market CLOSED, /regime/bpi SPX):\n";
tests_prompt += "AS OF SESSION shows 2026-08-12 with subcopy DAILY CLOSE SERIES. Operator date is Aug 13.\n";
tests_prompt += "After 16:00 ET on a trading day the last COMPLETED session is today. Yesterday is stale.\n\n";
tests_prompt += "KNOWN CODE (do not invent a second theory without reading these):\n";
tests_prompt += "- scripts/bpi_scan.py: as_of_session = latest aggregated Yahoo date; stale = latest < last_completed_session_date().\n";
tests_prompt += "  Sessions = UNION of member bar dates, not the market calendar. If Yahoo has no T bars, as_of stays T-1.\n";
tests_prompt += "- radon-bpi.timer: Mon-Fri 21:30 UTC after close + ONLY Tue-Sat 11:00 UTC morning catch-up.\n";
tests_prompt += "  Comments admit the 21:30 pass often leaves the series one session stale until NEXT MORNING.\n";
tests_prompt += "- parse_yahoo_spark drops null last closes; parse_yahoo_chart recovers meta.regularMarketPrice (2026-07-24 repro).\n";
tests_prompt += "- web/lib/bpi.ts isBpiPayload does not type `stale`. BpiPanel never renders stale. Silent yesterday.\n";
tests_prompt += "- web/lib/marketSession.ts lastCompletedSessionDate() is the client contract (today only at/after 16:00 ET).\n";
tests_prompt += "- web/lib/rvRatio.ts isSnapshotCurrent / web/lib/criStaleness.ts already implement this EOD rule.\n";
tests_prompt += "- Do NOT fabricate today's BPI by carry-forwarding yesterday's P&F onto T with no T closes.\n";
tests_prompt += "- Do NOT change _utc_date to ET unless you prove Yahoo midnight-UTC stamps drop today's bar (existing tests pin 13:30 UTC opens).\n\n";
tests_prompt += "WRITE FAILING TESTS:\n";
tests_prompt += "1) web/lib/bpi.ts helper (test in web/tests/bpi-panel.test.tsx or a new web/tests/bpi-staleness.test.ts):\n";
tests_prompt += "   isBpiSessionCurrent(asOf, now) uses lastCompletedSessionDate.\n";
tests_prompt += "   Frozen now = 2026-08-13 19:22 ET (after close, weekday): as_of 2026-08-13 current; 2026-08-12 NOT current.\n";
tests_prompt += "   Frozen now = 2026-08-13 15:00 ET (still RTH): as_of 2026-08-12 IS current (today's close does not exist yet).\n";
tests_prompt += "   Frozen Saturday: Friday as_of is current (off-hours must not false-stale — lessons 2026-07-11 / CRI weekend).\n";
tests_prompt += "2) BpiPanel: when as_of lags lastCompletedSessionDate, AS OF SESSION shows the date AND visible STALE (data-testid=bpi-session-stale).\n";
tests_prompt += "   When current, no STALE. Do not hardcode cadence copy like 'Refreshes nightly'.\n";
tests_prompt += "3) scripts/tests/test_bpi_scan.py: parse_yahoo_spark recovers a null LAST close the same way parse_yahoo_chart does\n";
tests_prompt += "   when regularMarketTime/regularMarketPrice (or equivalent spark meta) maps to that last bar's session.\n";
tests_prompt += "   Extend the existing MSFT null-last fixture so members_still_stale no longer lists MSFT for 2026-07-27 once recovered.\n";
tests_prompt += "   Keep mid-series nulls dropped.\n";
tests_prompt += "4) cloud/tests/test_systemd_services.py TestBpiScanBudget: timer must also fire a SAME-EVENING catch-up\n";
tests_prompt += "   Mon-Fri 23:30:00 UTC (19:30 ET) in addition to 21:30 UTC and Tue-Sat 11:00 UTC.\n";
tests_prompt += "5) web/e2e/bpi-tab.spec.ts (or existing bpi e2e): stub /api/bpi with as_of_session yesterday after a frozen post-close clock if the e2e can inject time;\n";
tests_prompt += "   otherwise assert the STALE testid when fixture as_of is far in the past relative to lastCompletedSessionDate().\n\n";
tests_prompt += "Run the new/updated unit tests and confirm they FAIL for the right reason.\n";
tests_prompt += "Prepend a short task to tasks/todo.md (T1 tests / T2 fix / T3 verify) if the BPI stale block is missing.\n";
tests_prompt += "Do not commit. Return {ok, files, notes, tests}.\n";

let r_tests = agent(tests_prompt, #{
    label: "red-tests",
    capability_mode: "all",
    agent_type: "general-purpose",
    output_schema: summary_schema,
});

if r_tests == () || !r_tests.success {
    complete(#{ ok: false, phase: "Tests", error: "red-tests agent failed" });
}
if r_tests.output.ok == false {
    complete(#{ ok: false, phase: "Tests", notes: r_tests.output.notes });
}
log("T1 red tests written");

phase("Fix");

let fix_prompt = "";
fix_prompt += "You are in the Radon repo. Surgical TDD fix. No commits. Implement only what the failing BPI stale tests require.\n";
fix_prompt += "Ignore the proposal-card workflow and unrelated dirty files.\n\n";
fix_prompt += "IMPLEMENT:\n";
fix_prompt += "1) web/lib/bpi.ts: isBpiSessionCurrent(asOf, now?) via lastCompletedSessionDate from marketSession.ts.\n";
fix_prompt += "   Optional stale?: boolean on BpiPayload — do not require it in isBpiPayload (old snapshots).\n";
fix_prompt += "2) BpiPanel AS OF SESSION: if !isBpiSessionCurrent(as_of), show STALE (testid bpi-session-stale). Brand tokens only. No em dash.\n";
fix_prompt += "3) scripts/bpi_scan.py parse_yahoo_spark: recover null LAST close like parse_yahoo_chart when meta session matches.\n";
fix_prompt += "   Read the spark payload shape in tests/existing fixtures. Do not persist in-progress bars (d <= last_complete still applies).\n";
fix_prompt += "4) cloud/services/radon-bpi.timer: add OnCalendar=Mon..Fri *-*-* 23:30:00 UTC same-evening catch-up. Update the comment.\n";
fix_prompt += "   Keep 21:30 UTC and Tue-Sat 11:00 UTC. Update cloud/tests if the red test is not already the pin.\n";
fix_prompt += "5) Do NOT carry-forward a fake today row without member closes for T. Do NOT change _utc_date to ET without proof.\n";
fix_prompt += "6) Do NOT click live orders. No cadence copy like Refreshes nightly.\n\n";
fix_prompt += "Re-run the failing unit tests until green:\n";
fix_prompt += "- python3.13 scripts/run_pytest_affected.py --files scripts/bpi_scan.py cloud/services/radon-bpi.timer -- -q\n";
fix_prompt += "  (or focused pytest scripts/tests/test_bpi_scan.py cloud/tests/test_systemd_services.py -q -k bpi)\n";
fix_prompt += "- From web/: npx vitest run tests/bpi-panel.test.tsx tests/bpi-staleness.test.ts tests/bpi-route.test.ts -q\n";
fix_prompt += "  (skip missing files). Align production to the tests, not the reverse, unless a test contradicts lastCompletedSessionDate.\n";
fix_prompt += "Mark T2 in tasks/todo.md. Return {ok, files, notes, tests}.\n";

let r_fix = agent(fix_prompt, #{
    label: "apply-fix",
    capability_mode: "all",
    agent_type: "general-purpose",
    output_schema: summary_schema,
});

if r_fix == () || !r_fix.success {
    complete(#{ ok: false, phase: "Fix", error: "fix agent failed" });
}
if r_fix.output.ok == false {
    complete(#{ ok: false, phase: "Fix", notes: r_fix.output.notes });
}
log("T2 production fix applied");

phase("Verify");

let verify_prompt = "";
verify_prompt += "Verify the BPI post-close stale fix. No commits. Confirm pwd. Ignore unrelated dirty files.\n";
verify_prompt += "1) python3.13 -m pytest scripts/tests/test_bpi_scan.py cloud/tests/test_systemd_services.py -q -k 'bpi or Bpi'\n";
verify_prompt += "2) From web/: npx vitest run tests/bpi-panel.test.tsx tests/bpi-route.test.ts tests/bpi-staleness.test.ts -q\n";
verify_prompt += "   Include any new staleness test file the previous agent created.\n";
verify_prompt += "3) From web/: npx playwright test e2e/bpi-tab.spec.ts --project=chromium (or the existing bpi e2e file if named differently).\n";
verify_prompt += "4) Confirm radon-bpi.timer contains 21:30, 23:30 Mon-Fri, and 11:00 Tue-Sat.\n";
verify_prompt += "5) Confirm BpiPanel shows STALE when as_of < lastCompletedSessionDate and not when current.\n";
verify_prompt += "6) Mark T3 + a one-paragraph Review with exact pass counts in tasks/todo.md.\n";
verify_prompt += "Do NOT commit or push. Return {ok, files, notes, tests} with counts.\n";

let r_verify = agent(verify_prompt, #{
    label: "verify",
    capability_mode: "all",
    agent_type: "general-purpose",
    output_schema: summary_schema,
});

if r_verify == () || !r_verify.success {
    complete(#{ ok: false, phase: "Verify", error: "verify agent failed" });
}

complete(#{
    ok: r_verify.output.ok,
    files: r_verify.output.files,
    notes: r_verify.output.notes,
    tests: r_verify.output.tests,
});
