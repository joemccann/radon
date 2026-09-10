# AI infrastructure implementation contract

Plan: ai-infrastructure-plan.md. Parallel ownership: core agent scripts/ai_cycle/{model,registry,store,transforms,snapshot,shadow}.py and core tests; collectors agent scripts/ai_cycle/{collectors,collect,__main__}.py and collector tests; UI agent web/components/AiInfrastructure*, web/lib/{aiInfrastructure,useAiInfrastructure}.ts, RegimePanel mount and LlmTokenIndexCard preservation, dashboard/ticker handoff and focused/E2E UI tests. Root owns API routes, assistant catalog, scheduler, integration, docs, final verification and PR. Communicate interface changes before editing shared files.

LLM/model-quality observations (OpenDesign Arena `D6`) stay on the Demand pane. They never share lineage, series or transforms with GPU scarcity / Silicon Data / Compute asking-price indicators.

## Collector observation wire contract
An observation is a plain JSON dict: indicator_id (D1..M1), series_id (stable metric plus entity/cohort), source_id, value (finite number), unit, period_start and period_end (ISO dates or UTC timestamps), published_at (ISO UTC or null if unknown), fetched_at (ISO UTC), source_url (canonical credential-free URL), raw_hash (SHA256 of source response), methodology_version (string), cohort_version (string), lineage_group (string), measurement (observed/derived/estimated), metadata (JSON object; entity, label, definition, access, coverage numerator/denominator, etc). No missing data rows with value=0. Status records separate: source_id, status (available/unavailable/error/restricted/experimental), reason (sanitized), checked_at. Fetch raw via bounded transport; snapshots archived by hash. No production DB writes during verification; use isolated local storage.

Store interface: append_observations(rows), record_source_status(status), read_observations(as_of=None), read_source_statuses(); constructor store supports isolated SQLite path and production Turso backed existing db client. Core agent defines exact invocation and messages other agents. Revisions preserve fetched_at, publication unknown tracked; as-of replay must never include first-seen later than simulated time.

## UI/API snapshot v1
GET /api/ai-cycle -> FastAPI GET /ai-cycle, cache-only (no provider calls). Empty valid state returns200; real errors propagate. Return:
{version:1, generated_at:ISO, as_of:ISO, indicators:Indicator[], sources:SourceStatus[], shadow:{status:"experimental",state:"insufficient_evidence"|"watch"|"clear",reason:string,evaluated_at:ISO,eligible_weeks:number}}
Indicator: {id,title,pane:"demand"|"compute"|"delivery"|"finance",priority:"P0"|"P1"|"P2",status:"available"|"unavailable"|"stale"|"incomplete"|"insufficient_history"|"experimental",reason:string,methodology:string,tickers:string[],source_ids:string[],metrics:Metric[],history:HistoryPoint[]}.
Metric: {id,label,value:number|null,unit,period_start,period_end,published_at:string|null,fetched_at,source_id,source_url,measurement,methodology_version,cohort_version,lineage_group,raw_hash,metadata:{...}}.
HistoryPoint: {date,value:number,unit,series_id,label,source_id}.
SourceStatus: {id,name,url,status,reason,checked_at:string|null,cadence,license,lineage_group}.
Do not display source unknowns as healthy. Frontend handles transport error separately; no fetch error hidden by cached values. Restrict source links to http(s). Chart histories grouped by series_id and unit.
