# Page error presentation audit, 2026-09-17

## Scope and method

Reviewed the code-map architecture and searched the complete `web/app`, `web/components`, and `web/lib` production surface for JSX error interpolation, error title/secondary props, caught `.message`, response-text failures, JSON rendering, and shared notification/error helpers. Traced the remaining direct error variables to their producers. This is a source audit of reachable error presentation, not a claim that upstream failures cannot occur or that every future diagnostic string is recognizable.

Transport status codes and backend diagnostic bodies remain available to server handlers and logs. The UI boundary translates capacity, timeout, network, authorization, rate-limit, and service failures into recovery copy, unwraps error envelopes, and rejects technical diagnostics. Existing inline alerts, empty states, refresh markers, toasts, and order banners retain their existing visual patterns.

## Inventory and disposition

| Route family / shared surface | Finding | Disposition |
|---|---|---|
| Scanner, discover, flow, journal, blotter and workspace sections | POST response text and hook errors could appear verbatim; failed rescan could obscure previous result set | Shared response parser and RequestError; failed rescans retain previous data; retry and freshness context remain visible |
| Regime: GEX, gamma rotation, VCG, BPI, volatility cone, streaks, RV ratio, options exposure, other chart refreshes | Direct errors and secondary copy; refresh marker title leaked original exception | Contextual safe copy at visible sinks and PanelRefreshError title; shared hook sanitizes refresh/load failures |
| Portfolio, performance, attribution, cash flows, desktop/mobile dashboard | Direct load errors and failed scanner/news/catalyst cards | Safe copy in existing surfaces; existing account-history authored empty-state copy remains |
| AI industry, filing forensics, ATS venue coverage, token index | Raw load errors and per-ticker source failures | Safe errors; measurement/source metadata remains research data, not an error response |
| Instrument company/news/ratings/seasonality/option and futures chains | Direct chain/load errors | Safe contextual copy; no contract, pricing, or financial logic changes |
| Order tickets, chat order placement, notifications | API error bodies could enter inline banners or transcript/notification failures | Shared order formatter/banner retained; listed-contract form now uses existing OrderErrorBanner; failed chat actions and error toasts sanitized |
| Chat/assistant | Main assistant path already uses status-based authored recovery copy; PI command catch could expose runtime message | Preserve assistant error mapping; sanitize PI failure and order-error paths only; never rewrite user/assistant transcript content |
| Research controls, reliability, workbench, valuation/export labs | Request errors and caught parser/export errors interpolated | Safe request/catch boundary; local validation, source document content and research exports remain intact |
| Newsfeed share/report generation | Raw API error and export/browser exception | Sanitize caught errors and returned error field; preserve existing retry controls |
| Alerts | Raw mutation errors alongside useful locally authored threshold validation | Sanitize catches and load error; retain threshold/range validation |
| Preferences and profile credentials | Caught API errors displayed in row/page messages | Safe catches; numeric preference validation and credential rejection guidance retained |
| Profile photo and username, ticker search, composer attachments | Existing local validation and authored failure text | Reviewed, retained; no raw remote error rendering found on these paths |
| Setup wizard | API error/caught exception plus vendor validation message | Safe failure and vendor-verdict copy; credential storage/control flow unchanged |
| Admin gateway/service status, demo users, kill switch, recent action log, writer freshness | Request bodies and diagnostic strings exposed inline or in titles | Safe error sinks and failed-action detail; writer error title/text sanitized; controls and success status unchanged |
| Application errors and global shell | Next runtime exception detail, shell live-data and connection banners | Runtime detail removed from app fallback; shell banners translated; static global fallback retained |

## Deliberate non-error content

- Source provenance metadata, imported research documents, audit downloads, markdown code blocks and usage metadata are data surfaces, not failed-request messages. They are not globally rewritten.
- Broker rejection meaning and locally authored input validation remain available. Presentation changes do not modify API status, trading risk gates, order submission, or backend exception handling.
- Admin success logs and health measurements remain operational data. Failed actions and `last_error` fields pass the presentation boundary.
- Shared hooks provide one defense; direct rendering boundaries cover independently fetched data and component props as a second defense.

## Validation

Added representative rendering regressions for nested backend envelopes on refresh tooltips and gateway status and a stack trace in an admin failed-action log (`web/tests/page-error-surfaces.test.tsx`). Scanner browser regressions and shared parser/order/toast tests are owned by the main implementation workflow. No local test suites were run; exact-head CI and browser evidence must be recorded in the PR before completion.
