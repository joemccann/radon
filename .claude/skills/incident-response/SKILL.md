---
name: incident-response
description: The exact Radon triage playbook for a production incident - reproduce locally, write a FAILING regression test first, fix, run the full test gate, commit a focused single-purpose commit, push once, wait for CI green, verify live in the browser. Use whenever an incident JSON from data/incidents/ or a production bug report is being worked; /incident orchestrates the parallel-analysis phase on top of this playbook.
---

# Incident response — the Radon playbook

The sequence is fixed. Skipping a step is how the 2026-07-08 outage happened
(rapid pushes) and how the destroy storm shipped green (regression test that
could not express the failing topology). Every step names its verification
evidence; paste that evidence before claiming the step done.

## 0. Read the incident + runbook case first

- Parse the incident JSON (`data/incidents/incident-*.json`): `case_id`,
  `severity`, `evidence`, `fingerprint`.
- Read the matching case in `docs/incident-runbook.md`. If the case documents a
  discriminating check (e.g. the Python Turso canary for `turso-destroy-storm`),
  RUN IT before touching code — the same symptom has more than one cause, and
  the wrong branch wastes the whole response (restart-flapping an upstream
  Turso outage makes it worse).
- Confirm blast radius from live evidence, not assumption:
  `curl -s localhost:3000/api/service-health | jq` (judge the BODY),
  `curl -s localhost:8321/health/lite`, `gh run list --workflow=ci.yml --limit 3`.
- If this is a NEW failure mode, you will add a runbook case in step 6.

## 1. Reproduce locally

- Reproduce the failure on your machine before writing the fix. For data bugs,
  reproduce against the artifact the user/production actually sees, never a
  synthetic fixture (`feedback` lesson: confirm against the user-visible
  artifact). Verify against live Turso, not `data/*.json` fallbacks.
- Confirm `pwd` is the repo root before any test run — cwd drift produces bogus
  failures.
- If the failure cannot be reproduced locally (VPS-topology-only), reproduce the
  MECHANISM in a test harness (the destroy-storm fix reproduced
  `agent.destroy()` mid-flight aborting 10/10 concurrent requests).

## 2. Write the FAILING regression test FIRST (red)

- Vitest for web/lib, pytest for scripts/api. The test must fail for the same
  REASON production failed, not merely fail. Encode the failing topology: the
  destroy storm shipped because `db-timeout-self-heal.test.ts` was single-caller
  while the incident was concurrent-caller.
- Derive expected values, never arithmetic in your head.
- Run it, paste the red output. No red, no fix.

## 3. Fix

- Root cause, not symptom. Surgical diff — touch only what the fix requires;
  no adjacent "improvements".
- Respect the standing invariants for the subsystem (runbook case lists them):
  e.g. never destroy the shared undici Agent on isolated evidence; all
  persistence through Turso; heartbeats on skip paths; bounded IB awaits.

## 4. Full test gate (green)

- Run the regression test file in isolation first, then the full gates from the
  repo root, mirroring CI:
  - `bunx vitest run --config vitest.config.ts` (root config, NOT `bun run test`)
  - `python3.13 -m pytest scripts/tests scripts/api/tests scripts/trade_blotter`
- If a test flakes, re-run the suspect file in isolation before concluding your
  change caused it (test-ordering pollution is common here).
- All green, pasted, before any commit.

## 5. Commit — focused, single-purpose

- `git status` FIRST. Stage ONLY the files you edited, by path
  (`git add path/to/file`). NEVER `git add -A` / `git add .` — untracked WIP
  (journals, scratch scripts, notebooks) must not be swept in.
- One logical change per commit; docs/runbook updates for THIS incident belong
  in the same commit. No secrets/PII. Claim in the message only what was
  actually verified.

## 6. Update the runbook + lessons

- New failure mode → new case section in `docs/incident-runbook.md` (mechanism,
  detection signals, discriminating checks, fix commits, regression tests).
  Known mode → append the new commit SHA to its case.
- If the incident came from a correction or a wrong first diagnosis, add the
  pattern to `tasks/lessons.md`.

## 7. Push ONCE and wait for CI green

- Check nothing is mid-deploy: `gh run list --workflow=ci.yml --limit 1`.
  If a run is in progress, WAIT — rapid-fire pushes cancelled an in-flight
  deploy and corrupted the production build (2026-07-08).
- `git push origin main` (main, never beta), then watch to completion:
  `gh run watch` or poll `gh run list --workflow=ci.yml --limit 1` until
  `completed/success`. The deploy job is gated on the full test matrix and
  holds a non-canceling production lock.

## 8. Verify live

- UI-visible fixes: verify in the real browser via chrome-cdp (fallback
  Playwright) and capture a screenshot as evidence. Tests alone do not close a
  UI incident.
- Data/API fixes: `curl` the production route (authenticated where needed) and
  verify the BODY; for persistence fixes confirm the row in live Turso.
- Confirm the incident watchdog resolves the artifact on its next cycle
  (`python3.13 -m scripts.incident_watchdog --once`, then check the incident
  file's `status` flipped to `resolved`), or resolve-and-note manually if the
  probe surface cannot see the fix.
- Only now report done, with the evidence inline.

## Stand-down criteria (do NOT ship a fix)

- Upstream/platform outage (Turso-side wedge, IB farm down): remediation is
  operational, not code. Follow the runbook case, alert, stand down.
- Off-hours staleness on RTH writers, anonymous 401/404 on protected routes,
  `unknown` probe states: not incidents. Close the artifact with a note.
- Deploy in a transitional state (CI `in_progress`): wait; do not "fix" a
  marker mismatch that a running deploy is about to close.
