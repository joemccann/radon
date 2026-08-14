let meta = #{
    name: "fix-proposal-object-object",
    description: "Fix Theta Harvester proposal alternatives rendering [object Object]",
    when_to_use: "ProposalCard alternatives show TICKER [object Object]",
    phases: [
        #{ title: "Tests", detail: "Failing tests with real structure objects" },
        #{ title: "Fix", detail: "Format structure via thetaStructLabel" },
        #{ title: "Verify", detail: "Focused vitest plus Playwright e2e" },
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

// ── Phase 1: red tests ──────────────────────────────────────────
phase("Tests");

let tests_prompt = "";
tests_prompt += "You are in the Radon repo at the workspace root. Red/green TDD. Surgical. No commits.\n";
tests_prompt += "Do NOT implement production code in this phase. Write FAILING tests only.\n";
tests_prompt += "Ignore unrelated dirty files (.deepsec, vol-cone fixtures, x-list-feed-plan, other workflows).\n";
tests_prompt += "Use rg / read_file before editing. Confirm pwd before vitest.\n\n";
tests_prompt += "BUG (screenshot, Theta Harvester /scanner?mode=theta Proposed action):\n";
tests_prompt += "ALTERNATIVES render as 'AMAT [object Object]', 'MSTR [object Object]', 'TTWO [object Object]'.\n";
tests_prompt += "Root cause already known: web/lib/agent/scannerProposal.ts interpolates row.structure\n";
tests_prompt += "as a string (`${row.ticker} ${row.structure}`). Live ThetaHarvesterResult.structure is\n";
tests_prompt += "ThetaHarvesterStructure { short_put, short_call, ... }, not a string. Tests currently LIE:\n";
tests_prompt += "web/tests/agent-derivations.test.ts candidate() sets structure: \"short strangle\" as ThetaHarvesterResult[\"structure\"].\n";
tests_prompt += "web/tests/agent-integration.test.tsx does the same. That is why the bug shipped.\n";
tests_prompt += "Correct formatter already exists: thetaStructLabel in web/lib/scannerHero.ts\n";
tests_prompt += "('SHORT 95P / 105C'). Table uses structureLabel in ThetaHarvesterScanner.tsx ('95P / 105C').\n";
tests_prompt += "statementFor fallback also interpolates result.structure the same broken way.\n\n";
tests_prompt += "WRITE / UPDATE tests:\n";
tests_prompt += "1) web/tests/agent-derivations.test.ts\n";
tests_prompt += "   - Change candidate() to a REAL ThetaHarvesterStructure (copy short_put/short_call shape from\n";
tests_prompt += "     web/e2e/theta-harvester-scanner.spec.ts AAPL fixture: put 95 / call 105).\n";
tests_prompt += "   - Keep setup string for existing statement tests unless you add a dedicated empty-setup case.\n";
tests_prompt += "   - Replace the alternatives assertion that expects label: \"NVDA short strangle\".\n";
tests_prompt += "     Expect a human label that includes ticker + strike legs, NEVER [object Object].\n";
tests_prompt += "     Prefer the existing thetaStructLabel contract: e.g. \"NVDA SHORT 95P / 105C\".\n";
tests_prompt += "   - Add an explicit test: with a real structure object, every alternative.label and the\n";
tests_prompt += "     fallback statement (empty/whitespace setup) do not contain \"[object Object]\".\n";
tests_prompt += "   - Add a fallback-statement test: empty setup uses ticker + formatted structure, not Object.\n";
tests_prompt += "2) web/tests/agent-integration.test.tsx\n";
tests_prompt += "   - Same real structure object (no string cast).\n";
tests_prompt += "   - Render ProposalCard with buildScannerProposal([top, amat, mstr, ttwo]) using real structures.\n";
tests_prompt += "   - Assert visible text includes AMAT and SHORT 95P / 105C (or the chosen formatter).\n";
tests_prompt += "   - Assert screen does NOT contain \"[object Object]\".\n";
tests_prompt += "3) web/e2e/theta-harvester-scanner.spec.ts\n";
tests_prompt += "   - Add a case with 4+ THETA_HARVEST rows (AMAT/MSTR/TTWO + one top) so ALTERNATIVES render.\n";
tests_prompt += "   - Assert the Proposed action card shows ticker + formatted structure, not [object Object].\n";
tests_prompt += "   - Reuse existing stubApis / payload shape. Do not hit live APIs.\n\n";
tests_prompt += "Run focused vitest from web/: npx vitest run tests/agent-derivations.test.ts tests/agent-integration.test.tsx -q\n";
tests_prompt += "Confirm FAIL for the right reason (labels are [object Object] or still expect the old string).\n";
tests_prompt += "Do not implement the production fix. Do not commit.\n";
tests_prompt += "Prepend a short task block to tasks/todo.md with T1 tests / T2 fix / T3 verify if missing.\n";
tests_prompt += "Return {ok, files, notes, tests}.\n";

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

// ── Phase 2: fix ────────────────────────────────────────────────
phase("Fix");

let fix_prompt = "";
fix_prompt += "You are in the Radon repo. Surgical TDD fix. No commits. Implement only what the failing tests require.\n";
fix_prompt += "Ignore unrelated dirty files.\n\n";
fix_prompt += "BUG: web/lib/agent/scannerProposal.ts alternativesFrom and statementFor interpolate\n";
fix_prompt += "`row.structure` / `result.structure`. Live type is ThetaHarvesterStructure (object),\n";
fix_prompt += "so React shows 'AMAT [object Object]'.\n\n";
fix_prompt += "FIX:\n";
fix_prompt += "- Import and use thetaStructLabel from web/lib/scannerHero.ts for both the alternative label\n";
fix_prompt += "  and the empty-setup statement fallback. Do not invent a second formatter.\n";
fix_prompt += "- Label form: `${row.ticker} ${thetaStructLabel(row)}` (e.g. AMAT SHORT 95P / 105C).\n";
fix_prompt += "- Do not stringify objects. Do not keep the string-cast test fixtures.\n";
fix_prompt += "- Touch only scannerProposal.ts unless a tiny shared helper is required (prefer existing).\n";
fix_prompt += "- No brand/copy changes beyond the label string.\n\n";
fix_prompt += "Re-run: from web/, npx vitest run tests/agent-derivations.test.ts tests/agent-integration.test.tsx -q\n";
fix_prompt += "Those must go green. If a test is still red because of a label wording mismatch, align the\n";
fix_prompt += "production string to thetaStructLabel, not the other way around.\n";
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

// ── Phase 3: verify ─────────────────────────────────────────────
phase("Verify");

let verify_prompt = "";
verify_prompt += "You are in the Radon repo. Verify the [object Object] proposal-label fix. No commits unless all gates green.\n";
verify_prompt += "Confirm pwd. Ignore unrelated dirty files. Do not click live Accept/order buttons.\n\n";
verify_prompt += "1) From web/: npx vitest run tests/agent-derivations.test.ts tests/agent-integration.test.tsx tests/scanner-hero.test.ts -q\n";
verify_prompt += "2) From web/: npx playwright test e2e/theta-harvester-scanner.spec.ts --project=chromium\n";
verify_prompt += "   If the new alternatives e2e needs a heading tweak, fix the test only if the UI already shows the right label.\n";
verify_prompt += "3) If a local Next server is on :3000, open /scanner?mode=theta and confirm no [object Object]\n";
verify_prompt += "   via Playwright or curl of the page source is NOT enough — use the e2e as the browser proof.\n";
verify_prompt += "4) Mark T3 in tasks/todo.md. Add a one-paragraph Review with test counts.\n";
verify_prompt += "5) Do NOT commit or push. The parent session will commit after review.\n";
verify_prompt += "Return {ok, files, notes, tests} with exact pass counts.\n";

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
