# Security loop: approved tool versions

The security-nightly skill (rail 8) requires a human to record the approved
`claude --version` and `claude plugin list --json` values and to freeze
updates with `DISABLE_AUTOUPDATER=1`. This file is that record. Update it
only after re-deriving the billing-reroute lists in the five loop wrappers
(the `BILLING_REROUTE_KEYS` / `BILLING_REROUTE_FLAGS` comment block explains
each name) and re-running `scripts/tests/test_weekend_subscription_only.py`.

| Tool | Approved version | Recorded | How to re-derive |
|---|---|---|---|
| Claude Code (`claude --version`) | 2.1.272 | 2026-09-15 | `strings -n 8 ~/.local/share/claude/versions/<ver> \| grep -o -E '(ANTHROPIC_\|CLAUDE_CODE_\|AWS_BEARER_)[A-Z0-9_]+' \| sort -u`, then read each new name in context and decide whether it reroutes model auth or billing off the claude.ai login. Record the decision per name in every wrapper's comment block. |
| `claude-security@claude-plugins-official` (`claude plugin list --json`) | 0.11.0 (scope user, enabled) | 2026-09-14 | `claude plugin list --json`; compare `version` and `installPath`. |
| gitleaks | 8.30.1 (sha256 `551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb`, linux_x64) | 2026-09-14 | Pinned in `.github/workflows/ci.yml` and `.github/workflows/gitleaks-history.yml`; the local binary must report the same `gitleaks version`. |
| deepsec (`vercel-labs/deepsec`) | 2.3.8 | 2026-09-14 | The DeepSec loop (`scripts/security_deepsec_nightly.sh`, skill `security-deepsec`) writes `deepsec-version.log` per audit and refuses a version that does not match this pin; the ignored `.deepsec/` workspace must pin the same version. Lockfile review is still OPERATOR_REQUIRED (see the skill, Stage 3). |

Previous Claude Code pins: 2.1.258 (initial list), 2.1.263 (re-derived
2026-09-07), 2.1.270 (re-derived 2026-09-14). 2.1.272 added no reroute name.
