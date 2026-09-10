# Financial research workbench

The workbench at `/research-workbench` connects source passages to research outputs and an operator-reviewed trading ticket. It extends Radon's existing newsfeed, research PDF ingestion, assistant, market indicators and order controls.

## Product boundaries

OpenAI's [Financial Services announcement](https://openai.com/index/introducing-chatgpt-financial-services/) and [Astra model documentation](https://developers.openai.com/api/docs/models/gpt-6-astra) were verified on 2026-09-10. Their premium datasets are not a Radon entitlement. This change does not claim Daloopa, PitchBook, Crunchbase or LSEG access, does not change the configured assistant model, and does not publish or place trades.

Sources are imported from existing research/newsfeed content or supplied by the operator. Extraction candidates retain exact source passages and offsets. A citation establishes where the text came from; it does not independently establish that the source is correct. Model analysis remains a review step. Provider content is untrusted data, never execution instructions.

## Workflows

| Request | Implementation | Boundary |
|---|---|---|
| Earnings and buyback blackout | Source-stated dates, calendar holds and ICS export | No universal blackout rule or inferred issuer policy |
| Cited indicator briefs | Literal passage references, publication dates, source inspection and Markdown export | Missing evidence remains unavailable |
| Fundamentals | Fiscal-period metrics, EBITDA reconciliation and explicit exclusions | Units and periods must align |
| Private deals | Sourced company/funding/investor cards and diligence questions | Gated content requires an authorized source import |
| Valuation labs | Editable DCF, simplified LBO, peer EV/EBITDA median, formula XLSX | Operator assumptions; no fabricated market values |
| Earnings transcripts | Extracted theme candidates and comparable metric deltas | Candidate themes are not validated trading signals |
| MCP reliability | Golden-query evaluator with mode, contract failures and latency measurements | Offline evaluation is not a production SLA |
| Agent-readable research | Original-page passage/table manifests with content hashes | OCR-needed pages cannot substantiate retrieval results |
| Desk note to ticket | Ticker-bound, dated checklist with source passages in existing order ticket | No inferred order action, size, legs or submit |
| Investor artifacts | Snapshot, What's moving, Pipeline, Headwinds and sources in XLSX/PPTX | Export reflects reviewed workspace data, not an automatic LP mailing |
| AI/fundamentals | Period-aligned capex, revenue, explicit AI-spend evidence | Company capex is not assumed to be AI-only |
| Governance | Existing role/confirmation controls and bounded operator audit export | Hosted MCP remains read-only |
| Marketing | Evidence, indicators, expression and operator-control section | No unsupported competitor or performance claims |

## Valuation conventions

Money inputs must share units. Shares must use matching scaling: millions of currency and millions of shares yield currency per share. Free cash flow is unlevered. DCF subtracts signed net debt once. The terminal growth rate must be below the discount rate. Negative free cash flow and net cash retain their signs.

The LBO is a scenario, not a financing model. Entry enterprise value is EBITDA times the entry multiple. Sponsor equity is entry enterprise value times one minus the acquisition-debt fraction. The operator supplies annual debt paydown. Exit debt floors at zero; limited-liability exit equity floors at zero. No interest schedule, fees, tax shields or interim distributions are modeled. The same growth assumption drives cash flow and EBITDA. Comparable peers require positive EV and EBITDA and unique names.

The XLSX includes editable assumptions, calculation formulas and cached results. Formula recalculation is requested on open. Imported source strings are literal cell values, never formulas. Source passages are preserved on a separate sheet. PPTX observations continue onto additional slides to keep the text readable.

## Ticket handoff

The browser session carries a versioned checklist keyed by ticker. It rejects invalid tickers, unsafe URLs, malformed payloads, future timestamps and context older than 24 hours. It displays source passages above the existing order surface, expires in-place and can be dismissed. Existing live quote, coverage, risk and confirmation controls remain responsible for every order.

## Verification

No local test suites are permitted. Author focused unit and browser tests, then run them through GitHub CI on the PR's exact head. Browser visual inspection uses an isolated development surface with fixture-backed API responses and no live order submission. Final evidence is recorded in `tasks/todo.md` and the pull request.
