---
description: Run the incident-response playbook end to end on an incident JSON (parallel root-cause, regression-test, and blast-radius subagents, then fix and ship per the playbook)
argument-hint: <path-to-incident-json> [--analyze-only]
---

Work the incident described by the JSON file at: $ARGUMENTS

First load the `incident-response` skill (`.claude/skills/incident-response/SKILL.md`) — it governs every phase below. Then:

## Phase 1 — Intake

1. Read the incident JSON. Extract `case_id`, `severity`, `evidence`, `fingerprint`, `detected_at`.
2. Read the matching case section in `docs/incident-runbook.md`. If the case names a discriminating check (e.g. the Python Turso canary), run it NOW and record the branch it selects.
3. Snapshot live state for the record: `curl -s localhost:3000/api/service-health | jq '.summary,.failing'`, `curl -s localhost:8321/health/lite`, `gh run list --workflow=ci.yml --limit 3`.

## Phase 2 — Parallel analysis (three subagents, ONE message)

Spawn all three concurrently with the Agent tool, passing each the full incident JSON content and the runbook case text:

- **(a) Root-cause analysis** (`general-purpose`): given the symptoms and evidence, trace the failure mechanism through the relevant code paths (runbook case lists them) and journald/log signatures. Deliverable: the mechanism as a causal chain (trigger → propagation → user-visible symptom), the specific file:line locations implicated, and the discriminating evidence that rules competing hypotheses out. It must state what evidence would DISPROVE its conclusion.
- **(b) Regression-test author** (`general-purpose`): design and write the FAILING test that encodes the failure topology (not a simplified single-caller version of it). Deliverable: test file(s) written to disk + the red run output. It must fail for the production reason.
- **(c) Blast-radius review** (`Explore`): find every other code path sharing the failing pattern (same helper, same table, same probe assumption, same topology bet). Deliverable: a list of file:line sites with a one-line risk note each, plus which existing tests cover them.

Wait for all three. Cross-check (a) against (b): the failing test must fail via the mechanism (a) identified — if not, reconcile before proceeding (one of them is wrong).

## Phase 3 — Fix and ship (skip when --analyze-only or when the runbook says stand down)

Follow the skill's steps 3–8 exactly: fix the root cause surgically, make the regression test green, run BOTH full gates from the repo root, commit focused (staged by path — never `git add -A`), update the runbook case with the new commit SHA (new case section if this was an undocumented mode), push once after confirming no deploy is in flight, watch CI to green, verify live (browser screenshot for UI, curl + live Turso for data), then confirm the incident artifact resolves on the next watchdog cycle.

If Phase 2 concludes the incident is an upstream/platform outage or a false positive, do NOT ship code: follow the skill's stand-down criteria, update the incident JSON with the finding, and report.

## Report

End with: root cause (one paragraph), the discriminating evidence, fix commit SHA (or stand-down rationale), test evidence (red → green), deploy status, live verification evidence, and blast-radius follow-ups worth separate tickets.
