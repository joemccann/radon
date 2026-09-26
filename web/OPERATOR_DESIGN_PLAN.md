# Operator: selected A / Action queue

Status: selected A implemented; exact-head GitHub CI pending.

## Reference and scope

[Selected interactive reference](../docs/design/operator-action-queue/index.html), [research](../docs/design/operator-action-queue/research.md), and [prototype review](../docs/design/operator-action-queue/review.md).

Restructure `/admin` so the operator can identify the most important observed condition and its available remedy before scanning telemetry. Adopt A on desktop and mobile. The prototype is an information hierarchy reference: retain the actual application shell, route access, source contracts, existing capabilities and controls. Never ship its synthetic values, demo handlers or incomplete inventories.

## Dependency graph

- [x] T1 — Define a pure attention projection from existing health, broker, service and writer observations; add regression cases. depends_on: []
- [x] T2 — Compose the action queue, compact overview, and diagnostic disclosures with existing controls and source state. depends_on: [T1]
- [x] T3 — Apply responsive Clear presentation, keyboard interactions and empty/loading/unknown states. depends_on: [T2]
- [x] T4 — Add focused component and Playwright coverage; inspect desktop/mobile rendering with fixtures. depends_on: [T2, T3]
- [ ] T5 — Create the implementation PR, wait for the expected checks on its exact head, repair failures, and deliver the required accepted green notification. depends_on: [T4]

Graph: T1 → T2 → T3 → T4 → T5; T2 also feeds T4. Run test suites on GitHub runners only. Local editing, static inspection and browser review remain allowed. A green PR is an implementation handoff, not authorization to merge or deploy.

## Composition

1. One Operator page title, production context, observation age, and visible Trading controls entry. Avoid duplicating the shell's title and telemetry.
2. A concise state summary distinguishes actionable conditions from placement permission. Source timestamps remain scoped to their observations.
3. Needs attention: stable rows, the highest applicable condition first, observed symptom, meaningful age, and one supported next step. The first mobile viewport contains the leading action.
4. Other affected writers and services remain visible as compact secondary rows. Healthy services collapse into counts and an accessible inventory entry.
5. Desktop right rail: broker authentication, core service liveness, scheduled freshness, latest external probe. This is a factual summary, not an invented reliability score.
6. Secondary inspection: complete services/writers inventory, historical reliability/SLOs, host resources, gateway details/advanced recovery, session actions, and existing user administration. Use accessible disclosures or a detail dialog; avoid mounting every historical metric in the initial visual hierarchy.
7. Mobile: action queue first; context and diagnostic sections follow. Reflow inventory entries into readable rows; primary actions never require horizontal scrolling.

## Attention model

Use a small pure helper (proposed `lib/adminAttention.ts`) with an explicit input type based on existing `adminTypes`, reliability and freshness helpers. A projected condition has a stable key, source, subject, state, observed-at timestamp, supported impact description, and existing action identifier/capability. Do not invent incident ownership, acknowledgements, P1/P2 severity, order counts, latency percentiles, or root-cause correlation.

- Prioritize confirmed actionable broker/essential service conditions and data integrity failures using explicit reviewed rules. Scheduled writer failures and overdue data remain actionable even when units are running.
- Unknown/stale critical observations must remain prominent when they prevent trustworthy operation; do not bury loss of visibility behind routine advisories. Scope capability restrictions to the action's actual preconditions.
- Evaluate writers against current schedule/applicability helpers. On-demand jobs are neutral unless a supported failure condition exists; old on-demand timestamps alone are not incidents.
- Treat last-run result and freshness as independent fields: an OK run can now be overdue, and a fresh report can contain an error.
- Avoid duplicate rows for the same confirmed observation, but do not group independent conditions under a guessed common cause.
- Use stable tie-breaking; preserve focus and selected details while polling. New urgent issues must not move a control under the pointer.

## Components and data ownership

- `components/admin/AdminWorkspace.tsx`: continue owning existing polling and action callbacks; replace only presentation composition.
- Proposed small `AdminAttentionQueue` and `AdminSystemOverview` components consume derived state and existing callbacks. Do not create parallel fetch loops or a second theme store.
- Reuse `Ib2faControls`, `IbGatewayCard`, `TradingKillSwitch`, `ServiceControlPanel`, `WriterFreshnessTable`, `ReliabilityStrip`, `SloStrip`, `HostMetricsStrip`, `RestartLog`, `DemoUsersTable`, and `ConfirmDialog` where practical. If controls need a compact presentation, add a narrowly scoped presentation prop rather than copying action logic.
- Use the existing theme tokens and component styles. No new design dependency or global navigation redesign is required.
- Keep authentication, authorization and backend mutation routes unchanged unless a proven contract defect blocks the selected presentation.

## Safety and state behavior

- Halt trading blocks new placement and leaves working orders live. Cancel all includes exits and does not itself halt new placement. Kill halts first and cancels all working orders, including exits, retaining typed confirmation.
- Preserve 2FA push lease/backoff, pending-action exclusion, gateway unknown-state protection, supported/can-control checks, host-role gates, and existing cascade confirmations.
- Pending request: show progress on the relevant control and prevent duplicate submission. Accepted/restarting is distinct from observed healthy.
- Healthy: no action needed, current source ages, access to full diagnostics. Hide emergency recommendations that are not supported by current state, while keeping trading controls discoverable.
- Loading: keep layout stable and label unresolved observations. Never show placeholder zero as a measured value.
- Unknown/stale: retain last known values with their age and explicit uncertainty; do not render them as healthy. Disable only actions whose existing safety preconditions cannot be established.
- Request/action failures: existing toast presentation with safe copy and retry. Stored diagnostic conditions can appear in details. No raw exception or JSON body in user-facing copy.
- The existing action log is session-local. Do not label it a durable audit trail or claim cross-session action history.

## Acceptance evidence

Use GitHub CI for unit/component/Playwright suites. Add coverage for attention order, stable keys, schedule-aware freshness, source-specific stale states, no-data behavior, and duplicate suppression. Exercise existing action preconditions and confirmations after their presentation moves.

Browser acceptance: 1280×830 and 1440×900 desktop, 390×752 and 320px mobile; Clear light/dark themes; healthy, attention, unavailable-source and action-pending fixtures. The highest-priority available action and Trading controls are visible without scrolling in the reference mobile viewport. No horizontal page overflow. Primary touch controls are at least 44px; metadata at least 12px; status has a text label; dialog focus is contained and returned; Escape closes details; polling preserves focused controls. Inspect actual browser screenshots before completion without invoking live remediation.

All existing service/writer diagnostics and administration features remain reachable. Inventory sorting/filtering applies to the full dataset. Keep source observation ages and sample counts beside historical metrics so historical SLO success cannot mask a current operational condition.

## Existing regression surfaces

Extend the relevant current suites rather than replacing coverage: `tests/admin-redesign-components.test.tsx`, `tests/admin-polling.test.tsx`, `tests/admin-action-request-assertions.test.tsx`, `tests/admin-trading-controls.test.ts`, and `e2e/admin-panel.spec.ts`, `e2e/admin-visual-snapshot.spec.ts`, `e2e/clear-admin-component.spec.ts`. Preserve `lib/serviceHealthWindows.ts` market-aware `isStale` behavior and current `adminReliability`, `adminHostMetrics`, `adminSlo`, `adminFormat`, and `adminTypes` contracts.
