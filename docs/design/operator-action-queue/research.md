# Operator design study

Date: 2026-09-25. Scope: three desktop/mobile design concepts for `/admin`; user selection precedes production implementation.

## Current page audit

The main design audit identified four sources of excess scanning: 15 summary tiles precede the controls, status is repeated across sections, the full writer table occupies the overview, and mobile retains horizontally scrolling tables. These observations concern presentation, not evidence that the underlying services are failing. The opportunity is to answer three questions in order: **What needs attention? What can I do? What confirms recovery?**

## Evidence and implications

1. **Rank the work by impact.** PagerDuty exposes incident priority on desktop and mobile and supports sorting incidents by priority. Put the actionable issue list immediately after the page header, with an explicit next step per issue. [PagerDuty incident priority](https://support.pagerduty.com/main/docs/incident-priority)
2. **Lead with the symptom.** Google distinguishes what is broken from why it broke. Show the affected capability and its observed state first; place raw status, explanatory detail, and diagnostics one level deeper. Do not label a suspected dependency as the established cause. [Google SRE monitoring](https://sre.google/sre-book/monitoring-distributed-systems/)
3. **Use progressive disclosure.** Grafana recommends a general-to-specific sequence and, when the question is which services are troubled, displaying those services instead of every service's data. Keep healthy services compact; expand a selected service to expose writer rows, source details, logs, and history. [Grafana dashboard best practices](https://grafana.com/docs/grafana/latest/visualizations/dashboards/build-dashboards/best-practices/)
4. **Distinguish stale, missing, and healthy.** Datadog documents separate no-data and last-known-state behavior. Show the observation time and explicit stale/unknown labels. A prior successful sample is evidence of past health, not present health. [Datadog monitor configuration](https://docs.datadoghq.com/monitors/configuration/) · [No-data diagnosis](https://docs.datadoghq.com/monitors/guide/troubleshooting-no-data/)
5. **Keep remedies attached to their context.** PagerDuty associates actions with services and incidents, distinguishes diagnostic from remediation actions, and exposes execution output. Place the relevant existing action beside the issue, preserving its actual preconditions. Show queued/running/result states; a successful command must be followed by a health observation before claiming recovery. [PagerDuty automation actions](https://support.pagerduty.com/main/docs/automation-actions/)
6. **Make observation delay visible.** Google's monitoring workbook warns that delayed feedback can cause responders to misjudge whether an action worked. Keep last observation and refresh state close to the status, especially after remediation. [Google SRE monitoring workbook](https://sre.google/workbook/monitoring/)
7. **Give existing metrics context.** Display available values with their unit, time window, and threshold where supported. Use consistent presentation and meaningful status color. Do not invent latency percentiles, uptime percentages, SLOs, forecasts, or a composite reliability score that the current payload cannot provide. [Grafana dashboard best practices](https://grafana.com/docs/grafana/latest/visualizations/dashboards/build-dashboards/best-practices/)
8. **Make mobile a complete triage flow.** Preserve urgency order in one column; move detail tables into a selected service view instead of requiring sideways scanning to find the action. Pair color with words or icons. Aim for 44px primary touch controls; WCAG AA specifies a 24px minimum or sufficient spacing, not a universal 44px minimum. [W3C reflow](https://www.w3.org/WAI/WCAG21/Understanding/reflow) · [Use of color](https://www.w3.org/WAI/WCAG22/Understanding/use-of-color.html) · [Target size](https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum.html)

These interface choices are design inferences from the cited guidance; the sources do not prescribe Radon's exact layout or issue ranking.

## Three concepts

- **A · Action queue:** urgency-ordered issue rows with a direct next step; a compact service summary below; selected details open on demand. Best for fast recurring checks and immediate remediation.
- **B · Service workspace:** stable service navigation and a focused detail workspace; urgent actions remain at the top. Best for diagnosis when the operator already knows the affected service.
- **C · Guided recovery:** one selected issue with current evidence, a short recovery sequence, and subsequent verification; other issues remain accessible. Best for infrequent or multi-step recovery tasks.

All concepts use the same scenario and information so layout, interaction, and density can be compared fairly. Desktop and mobile preserve the same action priority. The three concepts follow Radon's **Clear** design memory and current web tokens. `web/AGENTS.md` explicitly makes Clear's white/evergreen palette, quiet dark counterpart, Inter hierarchy, tabular values, and 6/8/10px radii authoritative over legacy dark-only/4px web rules.

## Shared scenario and provenance

The comparison uses **synthetic demonstration data**: IB authentication is pending and scheduled dispersion is overdue. These are independent displayed conditions. Do not claim that IB authentication caused the overdue dispersion run without evidence. Do not present demonstration durations, counts, timestamps, or action outcomes as current production observations.

Existing fields can support service status, authentication state, last observation, writer freshness, and available controls where the audited implementation actually supplies them. A prioritized cross-service issue list, human-readable impact statement, recovery sequence, and issue grouping are proposed presentation logic. They must be mapped to real source fields and existing actions during implementation. They are not new backend guarantees.

## Proposed ordering rules

1. Confirmed conditions requiring operator action and affecting an essential capability.
2. Confirmed overdue or failed scheduled work needing intervention.
3. Stale or unknown observations requiring investigation; do not silently classify them as healthy.
4. Advisory conditions and healthy service summaries.

Within a class, order by documented impact, then actionable next step, then age, with a stable service-name tie-breaker. Display severity labels only when a source or agreed rule supports them. Avoid inventing P1/P2 incident ratings for this prototype. Keep the selected issue stable during refresh so controls do not move under the pointer. If impact or cause is unverified, say so and preserve the observed state.

## Review boundary

This is a design study, not a production change or incident diagnosis. Controls in the concepts are simulations. No live remediation, deployment, or production implementation is authorized by this exploration. The next decision is the user's selection of A, B, C, or a specific combination.
