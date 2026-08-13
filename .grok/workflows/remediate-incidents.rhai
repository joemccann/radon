let meta = #{
    name: "remediate-incidents",
    description: "Diagnose live health errors and TDD-ship incident-class fixes",
    when_to_use: "Open or flapping Radon incidents and live service_health errors",
    phases: [
        #{ title: "Diagnose", detail: "parallel root-cause for remaining live issues" },
        #{ title: "Ship-known", detail: "TDD the three already-diagnosed defects" },
        #{ title: "Ship-rest", detail: "TDD remaining confirmed code fixes" },
        #{ title: "Verify", detail: "focused tests and runbook notes" },
    ],
};

let diagnose_schema = #{
    "type": "object",
    "required": ["issue_id", "disposition"],
    "properties": #{
        "issue_id": #{ "type": "string" },
        "disposition": #{ "type": "string" },
        "root_cause": #{ "type": "string" },
        "fix_plan": #{ "type": "string" },
        "test_plan": #{ "type": "string" },
        "files": #{ "type": "array", "items": #{ "type": "string" } },
    },
};

let ship_schema = #{
    "type": "object",
    "required": ["ok", "issue_id", "notes"],
    "properties": #{
        "ok": #{ "type": "boolean" },
        "issue_id": #{ "type": "string" },
        "files": #{ "type": "array", "items": #{ "type": "string" } },
        "tests": #{ "type": "string" },
        "notes": #{ "type": "string" },
    },
};

fn repo_rules() {
    let s = "";
    s += "Workspace: /Users/joemccann/dev/apps/finance/radon. Stay on the current git branch. ";
    s += "Ignore unrelated dirty files (.deepsec, vol-cone fixtures, x-list-feed-plan, implement-stop-orders.rhai). ";
    s += "Surgical. Red/green TDD. No git commit. No push. No git add -A. ";
    s += "Use rg / read_file first. Python: python3.13. JS: bunx vitest from repo root with vitest.config.ts. ";
    s += "No em dashes in user-facing copy. No raw hex in UI. Do not revert user changes.\n";
    s
}

fn diagnose_prompt(issue_id, brief) {
    let p = repo_rules();
    p += "READ-ONLY diagnosis. Use read_file, grep, list_dir only. Do not edit files.\n";
    p += "Issue id: ";
    p += issue_id;
    p += "\n";
    p += brief;
    p += "\nRead the matching case in docs/incident-runbook.md and the implicated source. ";
    p += "Disposition must be exactly one of: code_fix, stand_down, ops_only. ";
    p += "code_fix = a local test can encode the failing topology. ";
    p += "stand_down = upstream/platform/false-positive, no code. ";
    p += "ops_only = VPS/config/secret only. ";
    p += "Return {issue_id, disposition, root_cause, fix_plan, test_plan, files}. ";
    p += "Empty files is valid only after you read the code.\n";
    p
}

// ── Diagnose remaining live / flapping issues ──────────────────
phase("Diagnose");

let djobs = [];

let host_brief = "";
host_brief += "Live KB: service host-metrics is error. ";
host_brief += "Last error: turso_write_failed; row in fallback jsonl. ";
host_brief += "Sampler: scripts/host_metrics_sampler.py write_row/heartbeat. ";
host_brief += "Decide transient Turso vs code bug (SQL, schema, hrana, flap-brake).";
djobs.push(#{
    prompt: diagnose_prompt("host-metrics", host_brief),
    label: "diag-host-metrics",
    capability_mode: "read-only",
    agent_type: "explore",
    output_schema: diagnose_schema,
});

let orders_brief = "";
orders_brief += "Live KB: orders-sync error. ";
orders_brief += "execution fact conflict for U4698258:0002abdd.6a7daed5.01.01:1. ";
orders_brief += "Code: scripts/db/writer.py upsert_position_execution_fact (~1219). ";
orders_brief += "Decide late-enrichment hash miss vs real rewrite vs retry storm.";
djobs.push(#{
    prompt: diagnose_prompt("orders-sync", orders_brief),
    label: "diag-orders-sync",
    capability_mode: "read-only",
    agent_type: "explore",
    output_schema: diagnose_schema,
});

let gap_brief = "";
gap_brief += "journal-gap-sli appeared in 82 degraded incidents, 147-obs latch today 01:15Z. ";
gap_brief += "KB now says ok, missing_exec_id_count=0. Window is 15m in serviceHealthWindows.ts. ";
gap_brief += "Decide real gaps vs heartbeat/window/stale-gate false positive.";
djobs.push(#{
    prompt: diagnose_prompt("journal-gap-sli", gap_brief),
    label: "diag-journal-gap",
    capability_mode: "read-only",
    agent_type: "explore",
    output_schema: diagnose_schema,
});

let relay_brief = "";
relay_brief += "29 stale-market-data-freshness incidents, mostly fingerprint stale-freshness:relay_tick. ";
relay_brief += "See web/lib/probeFreshness.ts and web/tests/relay-idle-heartbeat-freshness.test.ts. ";
relay_brief += "Decide real relay drop vs probe false positive (idle heartbeat, auth, RTH).";
djobs.push(#{
    prompt: diagnose_prompt("relay-tick", relay_brief),
    label: "diag-relay-tick",
    capability_mode: "read-only",
    agent_type: "explore",
    output_schema: diagnose_schema,
});

let diags = parallel(djobs);
let confirmed = [];
let i = 0;
let allowed = #{
    "host-metrics": true,
    "orders-sync": true,
    "journal-gap-sli": true,
    "relay-tick": true,
};
for d in diags {
    if d != () && d.success && d.output != () {
        let oid = d.output.issue_id;
        let disp = d.output.disposition;
        if oid != () && allowed[oid] == true && disp == "code_fix" {
            confirmed.push(d.output);
        } else {
            let skip_id = if oid == () { "unknown" } else { oid };
            let skip_disp = if disp == () { "none" } else { disp };
            log("diagnose skip " + skip_id + " disposition=" + skip_disp);
        }
    } else {
        log("diagnose slot failed index=" + i.to_string());
    }
    i += 1;
}
log("confirmed extra code_fix count=" + confirmed.len().to_string());

// ── Ship already-diagnosed defects ─────────────────────────────
phase("Ship-known");

let p401 = repo_rules();
p401 += "ISSUE nextjs-db-read. Mark T2 in tasks/todo.md when done.\n";
p401 += "FACT: cloud/scripts/nextjs_db_watchdog.py GET http://127.0.0.1:3000/api/service-health with no auth.\n";
p401 += "FACT: web/middleware.ts comment still calls service-health public, but isPublicRoute does NOT include it.\n";
p401 += "FACT: web/tests/middleware-auth.test.ts:104 expects isPublicRoute(/api/service-health) === false (tightened 2026-08-11).\n";
p401 += "Live error: in-process Turso wedge; restarted radon-nextjs (probe error: HTTP Error 401: Unauthorized).\n";
p401 += "DO NOT add /api/service-health to isPublicRoute. Auth perimeter stays closed.\n";
p401 += "FIX:\n";
p401 += "1) Treat HTTP 401/403 as unknown, never as a Node-local Turso wedge. Do not increment consecutive_wedges. Do not restart. Do not write a wedge health row claiming Turso wedge.\n";
p401 += "2) Prefer authenticated probe: add /api/service-health to PROBE_BEARER_API_ROUTES and send Authorization Bearer RADON_PROBE_FRESHNESS_TOKEN when set (same as incident_watchdog probes.py).\n";
p401 += "3) Missing/wrong token or 401 = stand down / unknown. Only a 200 body with turso-db error row, or a transport failure while Python canary can still read Turso, is a wedge.\n";
p401 += "RED FIRST: tests in cloud/tests and/or scripts/tests that 401 does not restart; bearer path succeeds; public route stays false.\n";
p401 += "Run those tests green. Return {ok, issue_id: nextjs-db-read, files, tests, notes}.\n";

let r401 = agent(p401, #{
    label: "ship-nextjs-401",
    capability_mode: "all",
    agent_type: "general-purpose",
    output_schema: ship_schema,
});
if r401 == () || !r401.success {
    complete(#{ ok: false, phase: "Ship-known", error: "nextjs-db-read agent failed" });
}
if r401.output.ok != true {
    complete(#{ ok: false, phase: "Ship-known", issue_id: "nextjs-db-read", notes: r401.output.notes });
}
log("T2 nextjs-db-read shipped");

let peq = repo_rules();
peq += "ISSUE equibles-registration. Mark T3 in tasks/todo.md when done.\n";
peq += "Diagnosis already confirmed: five Equibles writers plus event-odds call record_service_health directly, are absent from SERVICE_FRESHNESS_WINDOWS, inherit 1h DEFAULT_WINDOW, and get coerced stale during RTH.\n";
peq += "Cadence: short-crowding daily 09:30 UTC; filing-forensics daily 10:00 UTC; 13f weekly Mon 09:45 UTC; ats-venue-share weekly Tue 09:15 UTC; cot-positioning weekly Sat 01:00 UTC. event-odds laptop-launchd today.\n";
peq += "RED FIRST: extend scripts/tests/test_service_registration_completeness.py with collect_direct_record_service_health_names() as specified in data/incidents_remote/20260813T183500Z-service-health-degraded.diagnosis.md.\n";
peq += "Exclude db/writer.py, db/service_cycle.py, db/scan_mirror.py, monitor_daemon/handlers/base.py, scripts/ib_watchdog.py (state-first arg).\n";
peq += "Sentinel must still see cor, straddle, vol-cone, skew, skew2d, margin-debt, yield-curve, catalysts, informed-flow.\n";
peq += "test_no_writer_falls_through_to_the_default_window must include the new collector.\n";
peq += "THEN register windows in web/lib/serviceHealthWindows.ts AND scripts/watchdog/services.py SCHEDULED_SERVICES. Mirror weekly/daily windows (preset-rebalance is the weekly precedent). requires_ib false.\n";
peq += "Keep TS scheduled category so test_watchdog/test_services.py contract stays green.\n";
peq += "Also add a runbook discriminator: registration-gap vs writer-down (ok+null last_error+stale = gap).\n";
peq += "Run: python3.13 -m pytest scripts/tests/test_service_registration_completeness.py scripts/tests/test_watchdog/test_services.py -q\n";
peq += "And bunx vitest run --config vitest.config.ts web/tests/service-health-windows.test.ts --reporter=dot\n";
peq += "Return {ok, issue_id: equibles-registration, files, tests, notes}.\n";

let req = agent(peq, #{
    label: "ship-equibles",
    capability_mode: "all",
    agent_type: "general-purpose",
    output_schema: ship_schema,
});
if req == () || !req.success {
    complete(#{ ok: false, phase: "Ship-known", error: "equibles agent failed" });
}
if req.output.ok != true {
    complete(#{ ok: false, phase: "Ship-known", issue_id: "equibles-registration", notes: req.output.notes });
}
log("T3 equibles shipped");

let pdep = repo_rules();
pdep += "ISSUE deploy-classifier. Mark T4 in tasks/todo.md when done.\n";
pdep += "classify.py:_classify_corrupt_build sets deploy_settled when ci.status in (None, completed) and not in_flight.\n";
pdep += "On the VPS gh is absent so ci is always None. Journal only covers promote-verify, not the multi-minute staged build. P2 fires with zero positive settled evidence.\n";
pdep += "RED FIRST in scripts/tests/test_incident_watchdog.py TestClassify:\n";
pdep += "test_marker_mismatch_with_unobservable_ci_and_no_journal_does_not_classify — ci None, in_flight false, marker!=head => classify() == [].\n";
pdep += "test_marker_mismatch_with_observed_completed_ci_still_classifies — observed completed CI still opens P2. Keep existing inflight/journal tests green.\n";
pdep += "FIX: ci=None is unknown, never settled. Only a positively observed completed CI (or equivalent) plus marker!=head plus not in_flight classifies P2.\n";
pdep += "P1 /sign-in 500 arm unchanged. Update docs/incident-runbook.md cancelled-deploy case.\n";
pdep += "Run: python3.13 -m pytest scripts/tests/test_incident_watchdog.py -q\n";
pdep += "Return {ok, issue_id: deploy-classifier, files, tests, notes}.\n";

let rdep = agent(pdep, #{
    label: "ship-deploy-classifier",
    capability_mode: "all",
    agent_type: "general-purpose",
    output_schema: ship_schema,
});
if rdep == () || !rdep.success {
    complete(#{ ok: false, phase: "Ship-known", error: "deploy-classifier agent failed" });
}
if rdep.output.ok != true {
    complete(#{ ok: false, phase: "Ship-known", issue_id: "deploy-classifier", notes: rdep.output.notes });
}
log("T4 deploy-classifier shipped");

// ── Remaining confirmed code fixes ─────────────────────────────
phase("Ship-rest");

let extra_results = [];
let extra_i = 0;
for item in confirmed {
    if extra_i >= 4 {
        log("dropped extra code_fix beyond 4");
        break;
    }
    extra_i += 1;
    let px = repo_rules();
    px += "ISSUE ";
    px += item.issue_id;
    px += ". Mark T5 in tasks/todo.md when this item is done.\n";
    px += "Disposition is code_fix. Implement the root cause, not a symptom patch.\n";
    px += "Root cause: ";
    px += if item.root_cause == () { "" } else { json_encode(item.root_cause) };
    px += "\nFix plan: ";
    px += if item.fix_plan == () { "" } else { json_encode(item.fix_plan) };
    px += "\nTest plan: ";
    px += if item.test_plan == () { "" } else { json_encode(item.test_plan) };
    px += "\nWrite the failing test first, then the minimal fix, then run the focused tests green.\n";
    px += "Return {ok, issue_id, files, tests, notes}.\n";
    let rx = agent(px, #{
        label: "ship-" + item.issue_id,
        capability_mode: "all",
        agent_type: "general-purpose",
        output_schema: ship_schema,
    });
    if rx == () || !rx.success {
        complete(#{ ok: false, phase: "Ship-rest", error: "agent failed", issue_id: item.issue_id });
    }
    if rx.output.ok != true {
        complete(#{ ok: false, phase: "Ship-rest", issue_id: item.issue_id, notes: rx.output.notes });
    }
    extra_results.push(rx.output);
    log("T5 shipped " + item.issue_id);
}

// ── Verify ─────────────────────────────────────────────────────
phase("Verify");

let pv = repo_rules();
pv += "Mark T6 in tasks/todo.md. Re-run the focused suites touched by this workflow:\n";
pv += "1) python3.13 -m pytest scripts/tests/test_incident_watchdog.py scripts/tests/test_service_registration_completeness.py scripts/tests/test_watchdog/test_services.py -q\n";
pv += "2) Any new nextjs-db-watchdog tests under cloud/tests or scripts/tests.\n";
pv += "3) bunx vitest run --config vitest.config.ts web/tests/middleware-auth.test.ts web/tests/service-health-windows.test.ts --reporter=dot\n";
pv += "4) If writer.py or execution facts changed, run the matching pytest file.\n";
pv += "If a test is red because of YOUR change, fix it. If a baseline is already red and unrelated, report it separately and do not expand scope.\n";
pv += "Add a short Review section at the top of tasks/todo.md under the Remediate live incidents task.\n";
pv += "Append a runbook note only for new discriminators you actually shipped.\n";
pv += "Return {ok, issue_id: verify, files, tests, notes} with the exact pass/fail counts.\n";

let rv = agent(pv, #{
    label: "verify",
    capability_mode: "all",
    agent_type: "general-purpose",
    output_schema: ship_schema,
});
if rv == () || !rv.success {
    complete(#{ ok: false, phase: "Verify", error: "verify agent failed" });
}

let report = "";
report += "# Incident remediation\n\n";
report += "- nextjs-db-read: ";
report += if r401.output.notes == () { "" } else { r401.output.notes };
report += "\n- equibles: ";
report += if req.output.notes == () { "" } else { req.output.notes };
report += "\n- deploy-classifier: ";
report += if rdep.output.notes == () { "" } else { rdep.output.notes };
report += "\n- extra_fixes: ";
report += extra_results.len().to_string();
report += "\n- verify: ";
report += if rv.output.notes == () { "" } else { rv.output.notes };
report += "\n";

let path = write_scratch_file("incident-remediation.md", report);
complete(#{
    ok: rv.output.ok,
    path: path,
    extra_fixes: extra_results.len(),
    notes: rv.output.notes,
});
