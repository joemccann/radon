let meta = #{
    name: "uw-daily-quota-forensics",
    description: "Find why the Unusual Whales 40k daily request quota burns before midday",
    when_to_use: "UW 75 percent daily limit email, 429 daily request limit, empty theta/strength snapshots after quota",
    phases: [
        #{ title: "Inventory", detail: "schedulers, pagination, on-demand, cache" },
        #{ title: "Model", detail: "request budget vs 40k and 09:04 warning" },
        #{ title: "Plan", detail: "ranked cuts that keep flow edge" },
    ],
};

let inventory_schema = #{
    "type": "object",
    "required": ["callers"],
    "properties": #{
        "callers": #{
            "type": "array",
            "maxItems": 16,
            "items": #{
                "type": "object",
                "required": ["name", "path", "cadence", "est_requests_per_run", "notes"],
                "properties": #{
                    "name": #{ "type": "string" },
                    "path": #{ "type": "string" },
                    "cadence": #{ "type": "string" },
                    "est_requests_per_run": #{ "type": "integer" },
                    "notes": #{ "type": "string" },
                },
            },
        },
        "summary": #{ "type": "string" },
    },
};

let model_schema = #{
    "type": "object",
    "required": ["primary_burner", "why_so_fast", "rth_estimate", "pre_open_estimate", "confidence"],
    "properties": #{
        "primary_burner": #{ "type": "string" },
        "why_so_fast": #{ "type": "string" },
        "rth_estimate": #{ "type": "integer" },
        "pre_open_estimate": #{ "type": "integer" },
        "confidence": #{ "type": "string" },
        "assumptions": #{ "type": "string" },
    },
};

let plan_schema = #{
    "type": "object",
    "required": ["do_now", "do_next", "do_later"],
    "properties": #{
        "do_now": #{ "type": "array", "maxItems": 5, "items": #{ "type": "string" } },
        "do_next": #{ "type": "array", "maxItems": 5, "items": #{ "type": "string" } },
        "do_later": #{ "type": "array", "maxItems": 5, "items": #{ "type": "string" } },
    },
};

phase("Inventory");

let sched_prompt = "";
sched_prompt += "Read-only UW quota forensics in the Radon repo. Use grep and read_file. Do not edit files.\n";
sched_prompt += "Inventory every SCHEDULED job that can call Unusual Whales (UWClient or api.unusualwhales.com).\n";
sched_prompt += "Check: cloud/services/*.timer and *.service, config/com.radon.*.plist, scripts/run_*.sh, scripts/data_refresh.py, scripts/run_data_refresh.sh, scripts/run_signals_refresh.sh.\n";
sched_prompt += "For each caller return name, path, cadence, est_requests_per_run (integer, best estimate from code), notes.\n";
sched_prompt += "Must cover: radon-signals-refresh (theta + strength), radon-refresh (CRI/VCG/GEX), radon-skew 1-min, leap, garch, oi-changes, vol-cone, catalysts, laptop data-refresh scanner/discover/flow_analysis, informed-flow, vcg-refresh.\n";
sched_prompt += "Note dual-host risk (Hetzner systemd AND laptop launchd). Check whether launchd agents are documented as loaded; do not invent process state.\n";
sched_prompt += "Read the actual scan_ticker functions to count HTTP GETs per ticker before estimating.\n";
sched_prompt += "Empty callers only if you grepped and found none.\n";

let page_prompt = "";
page_prompt += "Read-only. Count UW request multipliers from pagination and universes.\n";
page_prompt += "Read scripts/fetch_flow.py (DARKPOOL_MAX_PAGES, FLOW_ALERTS_MAX_PAGES, lookback, darkpool cache), scripts/discover.py (min-alerts 1, market-wide then per-ticker DP), scripts/scanner.py, scripts/fetch_oi_changes.py (_OI_MAX_PAGES), scripts/evaluate.py flow_days.\n";
page_prompt += "Read scripts/theta_harvester_scanner.py scan_ticker and scripts/strength_confirmation_scanner.py scan_ticker. Count distinct UW client calls per NDX100 ticker.\n";
page_prompt += "Estimate worst-case and typical requests per run. Return callers[] with those estimates.\n";
page_prompt += "Note: 60s in-memory uw_cache is per-process only; 24-worker pools each create UWClient.\n";

let ondemand_prompt = "";
ondemand_prompt += "Read-only. Inventory ON-DEMAND UW callers: Next.js routes and FastAPI scan POSTs.\n";
ondemand_prompt += "Read web/app/api/ticker/info/route.ts, previous-close/route.ts, ticker/news, ticker/seasonality. Note cache: no-store.\n";
ondemand_prompt += "Read scripts/api/server.py THETA_COOLDOWN_S and STRENGTH_COOLDOWN_S vs radon-signals-refresh.timer 15-min cadence.\n";
ondemand_prompt += "Check evaluate.py, fetch_options.py, leap_scanner_uw.py, garch_convergence.py.\n";
ondemand_prompt += "Same callers[] schema. Estimate requests per page view or eval.\n";

let cache_prompt = "";
cache_prompt += "Read-only. How quota is (not) protected.\n";
cache_prompt += "Read scripts/clients/uw_client.py retry loop (max_retries=3, 429 retried), scripts/utils/uw_cache.py TTL 60s in-memory, scripts/fetch_skew.py daily-limit embargo, docs/incident-runbook.md item (f) UW daily quota 40000 reset 20:00 ET.\n";
cache_prompt += "Determine whether a daily-cap 429 still spends quota on retries. Whether there is a process-wide daily budget. Whether demo/prod could share UW_TOKEN (docs/demo-environment.md, .env.example files).\n";
cache_prompt += "Return callers[] for protection gaps (name=gap, path=file, cadence=n/a, est_requests_per_run=0 or retry multiplier, notes=the hole).\n";

let inv_jobs = [
    #{ prompt: sched_prompt, label: "schedulers", capability_mode: "read-only", output_schema: inventory_schema },
    #{ prompt: page_prompt, label: "pagination", capability_mode: "read-only", output_schema: inventory_schema },
    #{ prompt: ondemand_prompt, label: "ondemand", capability_mode: "read-only", output_schema: inventory_schema },
    #{ prompt: cache_prompt, label: "cache-retry", capability_mode: "read-only", output_schema: inventory_schema },
];
let inv = parallel(inv_jobs);

let inv_text = "";
let i = 0;
for r in inv {
    if r != () && r.success && r.output != () {
        inv_text += json_encode(r.output);
        inv_text += "\n";
    } else {
        log("inventory slot " + i.to_string() + " failed or empty");
    }
    i += 1;
}

phase("Model");

let model_prompt = "";
model_prompt += "You are synthesizing UW daily-quota forensics. Do not edit files. Read-only if you need to verify a number.\n";
model_prompt += "FACTS FROM THE OPERATOR EMAIL: 2026-08-14 9:04 AM, usage 30000 / daily limit 40000 (75 percent). Plan is API Basic.\n";
model_prompt += "FACTS FROM REPO: UW daily reset documented as 20:00 ET in scripts/fetch_skew.py and docs/incident-runbook.md. tasks/todo.md today says UW daily cap burned at 15:45 UTC and signals refresh wrote empty 102/0 snapshots.\n";
model_prompt += "INVENTORY JSON FROM FOUR AGENTS:\n";
model_prompt += inv_text;
model_prompt += "\nCompute a burn model. primary_burner must be the single largest scheduled consumer if evidence supports it.\n";
model_prompt += "why_so_fast: one paragraph, no fluff, explain 30k by morning AND 40k by 15:45 UTC.\n";
model_prompt += "rth_estimate: integer UW requests from scheduled RTH jobs 09:00-16:00 ET.\n";
model_prompt += "pre_open_estimate: integer from 20:00 ET previous day to 09:30 ET.\n";
model_prompt += "If 9:04 AM could be PT not ET, say so in assumptions.\n";
model_prompt += "confidence: high/medium/low.\n";

let model = agent(model_prompt, #{
    label: "burn-model",
    capability_mode: "read-only",
    output_schema: model_schema,
});

if model == () || !model.success {
    complete(#{ ok: false, phase: "Model", error: "burn-model agent failed", inventory: inv_text });
}

phase("Plan");

let plan_prompt = "";
plan_prompt += "Read-only. Recommend a ranked UW quota cut plan for Radon.\n";
plan_prompt += "Constraint: keep flow-edge jobs (dark pool / OI for evaluate and watchlist). Cut duplicated NDX-wide polling first.\n";
plan_prompt += "Burn model: " + json_encode(model.output) + "\n";
plan_prompt += "Inventory excerpt: " + inv_text + "\n";
plan_prompt += "do_now: max 5 items, each one concrete change (file + behavior), shippable today.\n";
plan_prompt += "do_next: max 5. do_later: max 5. No em dashes. No preamble.\n";
plan_prompt += "Prefer: lengthen signals cadence or serve cache; share fetches between theta and strength; disk/Turso UW cache by endpoint class; never retry daily-cap 429; process-wide remaining budget; IB-first OHLC/quote.\n";

let plan = agent(plan_prompt, #{
    label: "cut-plan",
    capability_mode: "read-only",
    output_schema: plan_schema,
});

if plan == () || !plan.success {
    complete(#{ ok: false, phase: "Plan", error: "plan agent failed", model: model.output });
}

let report = "";
report += "# UW daily quota forensics\n\n";
report += json_encode(#{ model: model.output, plan: plan.output, inventory: inv_text });
let path = write_scratch_file("uw-quota-report.md", report);

complete(#{
    ok: true,
    path: path,
    primary_burner: model.output.primary_burner,
    why_so_fast: model.output.why_so_fast,
    rth_estimate: model.output.rth_estimate,
    pre_open_estimate: model.output.pre_open_estimate,
    confidence: model.output.confidence,
    plan: plan.output,
});
