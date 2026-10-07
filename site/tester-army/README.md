# Public site flow verification

TesterArmy `e2e` 0.16.0 and `@e2e-dev/web` 0.11.2 run real Chromium through Playwright 1.63.0. This isolated package preserves the existing web Playwright version and site hard gates. Node >=22.12 is required. All tests use deterministic locators/assertions; no model provider or account credentials are needed. Telemetry is disabled by the test script.

From the repository root:

- `env -i PATH="$PATH" HOME="$HOME" npm ci --prefix site`
- `env -i PATH="$PATH" HOME="$HOME" npm ci --prefix site/tester-army`
- `env -i PATH="$PATH" HOME="$HOME" npm test --prefix site/tester-army`

Local server: loopback port3342, `.next-tester-army`, one worker. In CI, first build with `cd site && NEXT_DIST_DIR=.next-tester-army npm run build`, then set `TESTER_ARMY_SERVER_MODE=start` for the same test command. The runner owns its server lifecycle and does not reuse a process already bound to that port. Reports, traces, and failure screenshots are under `site/tester-army/.e2e/` and ignored by Git.

## Inventory

`tests/public-flows.e2e.ts` explicitly inventories17 HTML routes: homepage,6 dossiers,7 developer/agent documents,2 legal documents, status. The status demo CTA explicitly requires the signup entry. Every route checks a single visible h1, local fragment validity, desktop/mobile horizontal containment; the endpoint/link audit checks HTTP200 separately. Homepage journeys click5 header sections and3 footer document links, then breadcrumbs back home. Contents navigation clicks every section in developer/agent/legal documents. Mobile menu checks open, Escape, link selection, and close. Theme checks toggle, persistence/reload, and light restoration. All7 recipes test actual copy payload, success feedback, prompt open/close; simulated permission denial checks failure feedback. The clipboard simulation avoids OS permissions and is explicitly browser-local; existing Playwright copy coverage retains actual browser clipboard permissions.

Missing routes exercise branded404 and homepage recovery; unknown markdown returns404. robots, sitemap, manifest, llms, OpenAPI and negotiated markdown respond locally. All discovered local hyperlinks receive HTTP checks. External links are checked as destination attributes only, never opened or submitted; demo CTAs must use `/sign-up`.

The homepage FAQ is static text, with no accordion. Developer docs use static paragraph URLs and Contents anchors, with no tabs/search. Status is a static surface directory, with no loading/error transitions. Animated plates/scroll reveal are retained under existing Playwright specs. The Contents-link journey sets only the document's scroll behavior to `auto` while repeatedly traversing anchors, avoiding smooth-scroll action timing while preserving click, URL, and target checks. The global-error component cannot be reached through a public interaction without injecting a server failure; no production error is induced. Keyboard modal tests in `site/e2e/agent-prompt-keyboard.spec.ts` additionally cover initial focus, Tab/Shift+Tab, Escape/close/backdrop, focus restoration, narrow modal containment, and keyboard access to scrollable tables. Run the existing Playwright suite with `FX_SHOT_DIR=$PWD/site/tester-army/.e2e/visual node web/node_modules/@playwright/test/cli.js test --config web/playwright.site.config.ts` from the repository root; its config starts a local server on port3336. Broad WCAG certification and external destination availability are outside these local flow assertions.

Fresh local verification, 2026-10-06: the retained site Playwright suite passed 13/13 in Chromium at one worker. The temporary status signup assertion failed against the original status-page source, then passed 1/1 against the staged fix; the temporary spec was removed. Mobile viewport captures at 390×844 include the Kelly and provider tables after scrolling each into view and waiting for its reveal, plus the prompt dialog; home and status before/after captures are also retained. The separate TesterArmy production build receipt remains 27/27 and is stored in `.e2e/report.json`.

See [repository integration guide](../../docs/testing-tester-army.md).

## Recovery review, 2026-10-06

The isolated configuration typecheck passes after making the Contents-navigation browser callback return a JSON-compatible value. Both SDK result caching (`cache: "off"`) and retries are disabled; failure traces are retained (`trace: "retain-on-failure"`). Browser verification against the current main base and recovered fixes is recorded after fresh runs, rather than treating the preserved October 3 reports as current evidence.
