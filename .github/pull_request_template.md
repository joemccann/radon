## Summary

<!-- One or two sentences: what broke or what this ships, and why. -->

<what this is and why>

## What changed

<files or behavior this PR changes>

## Test plan

<!-- Evidence is a command, count, SHA, or status. Do not claim a check that did not run. -->

- [ ] Focused local: <command and result>
- [ ] CI: <jobs that apply, or pending>

## Risk and rollback

<!-- Merging to main runs CI, then the Production deploy starts with no approval step. -->

<revert commit, or the rollback path if revert is not enough>

## Operator Next Steps

<!-- Work still required after the production deploy lands. Write None when the deploy is the whole ship. -->

<None, or the operator work still open: merge (automation never merges), secret or flag change, host package or unit install, data backfill, 2FA or gateway action, follow-up outside CI.>

## Checklist

- [ ] Red/green TDD (failing test, then fix)
- [ ] Stage only the files this change owns (no `git add -A`)
- [ ] Owner doc updated, or the commit message has `docs-skip: <reason>`
- [ ] Previous main deploy finished before this push
