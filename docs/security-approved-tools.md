# Security loop: tool versions

**No nightly loop enforces a version pin on Claude Code, the claude-security
plugin, or deepsec** (operator decision 2026-09-19). The agent CLIs update
faster than a recorded pin can follow, and every drift blocked the scan
stage with `OPERATOR_REQUIRED`. Each loop now records the observed
`claude --version`, `claude plugin list --json`, and `deepsec --version` in
its private run-record and proceeds. `DISABLE_AUTOUPDATER=1` still holds in
the plists so a version does not change mid-cycle.

The one remaining pin is gitleaks, enforced by CI, not by the loops.

| Tool | Last checked against | Recorded | How to re-derive |
|---|---|---|---|
| Claude Code (`claude --version`) | 2.1.272 | 2026-09-15 | When the installed CLI changes, re-derive the billing-reroute lists: `strings -n 8 ~/.local/share/claude/versions/<ver> \| grep -o -E '(ANTHROPIC_\|CLAUDE_CODE_\|AWS_BEARER_)[A-Z0-9_]+' \| sort -u`, read each new name in context, add any that reroutes model auth or billing to `BILLING_REROUTE_KEYS` / `BILLING_REROUTE_FLAGS` in every wrapper, and re-run `scripts/tests/test_weekend_subscription_only.py`. |
| `claude-security@claude-plugins-official` (`claude plugin list --json`) | 0.11.0 (scope user, enabled) | 2026-09-14 | `claude plugin list --json`. |
| gitleaks (**pinned, CI**) | 8.30.1 (sha256 `551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb`, linux_x64) | 2026-09-14 | Pinned in `.github/workflows/ci.yml` and `.github/workflows/gitleaks-history.yml`; the local binary must report the same `gitleaks version`. |
| deepsec (`vercel-labs/deepsec`) | 2.3.8 | 2026-09-14 | The DeepSec loop writes `deepsec-version.log` per audit. Rail 8 (never auto-update unattended) still applies; only the version-match refusal was removed. |

History: Claude Code was pinned at 2.1.258, 2.1.263 (2026-09-07), 2.1.270
(2026-09-14) and 2.1.272 (2026-09-15) before the pin was dropped.
