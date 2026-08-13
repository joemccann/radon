let meta = #{
    name: "rewrite-docs-index",
    description: "Apply the thin-index docs rewrite and adversarially verify no bloat",
    when_to_use: "Keep README and runbooks current without adding a changelog",
    phases: [
        #{ title: "Apply", detail: "five planned file edits" },
        #{ title: "Verify", detail: "skeptic checks the plan invariants" },
    ],
};

let ship_schema = #{
    "type": "object",
    "required": ["ok", "notes"],
    "properties": #{
        "ok": #{ "type": "boolean" },
        "files": #{ "type": "array", "items": #{ "type": "string" } },
        "notes": #{ "type": "string" },
    },
};

let verdict_schema = #{
    "type": "object",
    "required": ["ok", "evidence"],
    "properties": #{
        "ok": #{ "type": "boolean" },
        "evidence": #{ "type": "string" },
        "gaps": #{ "type": "array", "items": #{ "type": "string" } },
    },
};

phase("Apply");

let apply_p = "";
apply_p += "Workspace /Users/joemccann/dev/apps/finance/radon. Stay on main. No git commit. No push.\n";
apply_p += "Read docs/show-me-docs-rewrite-plan.html first. Then read each target file. Apply the plan if anything is missing. If already applied, do not rewrite.\n";
apply_p += "Allowed files only: README.md, docs/indicators/README.md, docs/scripts-reference.md, docs/operations.md, docs/cloud-services.md, docs/status.md.\n";
apply_p += "Invariants: no 30-day changelog; no em dashes in new copy; no emoji; one owner per fact; do not grow cloud-services.md except the nextjs-db-watchdog bearer sentence.\n";
apply_p += "README must have ## Now true (not Recent additions), three What's-where rows for incident-runbook, indicators, equibles, and layout lines for knowledge/ and indicators/.\n";
apply_p += "docs/status.md must be a short stub, not an eval dump.\n";
apply_p += "scripts-reference must not say npm test.\n";
apply_p += "Return {ok, files, notes}.\n";

let r_apply = agent(apply_p, #{
    label: "apply-docs",
    capability_mode: "all",
    agent_type: "general-purpose",
    output_schema: ship_schema,
});
if r_apply == () || !r_apply.success {
    complete(#{ ok: false, phase: "Apply", error: "apply agent failed" });
}
if r_apply.output.ok != true {
    complete(#{ ok: false, phase: "Apply", notes: r_apply.output.notes });
}
log("docs apply ok");

phase("Verify");

let verify_p = "";
verify_p += "READ-ONLY. Use read_file and grep only. Workspace /Users/joemccann/dev/apps/finance/radon.\n";
verify_p += "Adversarially check the thin-index docs rewrite against docs/show-me-docs-rewrite-plan.html.\n";
verify_p += "Set ok=true only if ALL of these are true after you read the files:\n";
verify_p += "1) README.md has no ## Recent additions and has ## Now true with at most 5 bullets.\n";
verify_p += "2) README What's where links docs/indicators/README.md, docs/incident-runbook.md, docs/equibles-api.md.\n";
verify_p += "3) docs/indicators/README.md is a table of vol-cone, skew, skew2d, straddle, cor, curve.\n";
verify_p += "4) docs/scripts-reference.md does not contain the string npm test.\n";
verify_p += "5) docs/status.md does not contain AAPL or 2026-04-23 eval dump.\n";
verify_p += "6) docs/operations.md mentions RADON_PROBE_FRESHNESS_TOKEN and Equibles windows.\n";
verify_p += "7) docs/cloud-services.md mentions the service-health bearer token.\n";
verify_p += "Return {ok, evidence, gaps}. gaps is empty only when ok is true.\n";

let r_verify = agent(verify_p, #{
    label: "verify-docs",
    capability_mode: "read-only",
    agent_type: "explore",
    output_schema: verdict_schema,
});
if r_verify == () || !r_verify.success {
    complete(#{ ok: false, phase: "Verify", error: "verify agent failed" });
}

let report = "";
report += "apply: ";
report += if r_apply.output.notes == () { "" } else { r_apply.output.notes };
report += "\nverify: ";
report += if r_verify.output.evidence == () { "" } else { r_verify.output.evidence };
report += "\n";
let path = write_scratch_file("docs-rewrite.md", report);
complete(#{
    ok: r_verify.output.ok,
    path: path,
    notes: r_verify.output.evidence,
});
