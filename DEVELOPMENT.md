# DEVELOPMENT — the authoring toolchain

This file maps the tools used to *author* Radon: what each one is, the role it plays, and where its canonical configuration lives. It is a current-state map with rationale, not a runbook and not a changelog. Rules and instructions live in the files it points to; nothing here should be duplicated from them.

## How Radon is built

Radon is a single-operator project authored AI-first. The operator directs coding agents that do the bulk of implementation; the repo is structured so that agents load the right context automatically (scoped runbooks, skills, hooks) and every change passes through the same verification gates a human would use (TDD, live browser checks, CI). Institutional knowledge that is not derivable from the code lives in the agents' own memory stores, not in this repo.

## Coding agents

| Tool | Role | Canonical config |
|---|---|---|
| Claude Code | Primary coding agent | `CLAUDE.md` (root) + subsystem `CLAUDE.md` files that auto-load by cwd |
| Codex | Second agent, used for parallel or rescue work | `AGENTS.md` mirrors beside each `CLAUDE.md`, plus `.pi/` |
| Foreman | Unattended GitHub-issue factory (draft PR only) | `factory/` + [`docs/factory.md`](docs/factory.md) |

The `AGENTS.md` files intentionally mirror the `CLAUDE.md` hierarchy rather than sharing one file: each agent has its own discovery convention, and mirroring keeps either agent fully functional without the other. When a runbook changes, both copies change in the same commit.

`CONTRIBUTING.md` defers to `CLAUDE.md` as the developer runbook. That is deliberate: there is one operator, and the runbook that matters is the one the agents read.

## Session tooling around the agents

| Tool | Role | Where |
|---|---|---|
| Serena | Semantic code navigation MCP server (language-server-backed symbol search, references, precise edits) | `.serena/` (config + its project memories) |
| RTK (Rust Token Killer) | CLI proxy that rewrites shell commands to token-optimized equivalents, transparently via a user-level hook | User-level Claude config (`~/.claude/RTK.md`); not in this repo |
| Secret-scan hook | Blocks any `git commit` whose staged diff matches key/password/token patterns | `.claude/settings.json` (PreToolUse) |
| Skills | Reusable agent capabilities (design skills, Remotion best practices, etc.) shared by both agents | `.agents/skills/` (source), symlinked into `.claude/skills/` |
| Workflows | Deterministic multi-agent orchestration scripts; the 66–71-agent security audits were run this way | `.claude/workflows/security-audit.mjs` |
| Figma MCP | Design file access from agent sessions | `.mcp.json` |

## Verification gates

Every change, agent-authored or not, goes through the same gates. The gates themselves are specified in `CLAUDE.md`; the tools are:

- **Vitest** (web) and **pytest** (Python) — red/green TDD is mandatory, full suite before commit.
- **chrome-cdp** (live Chrome session) — primary E2E verification for all UI work; **Playwright** (`web/playwright.config.ts`) is the fallback and the only option on the VPS.
- **CI** (`.github/workflows/ci.yml`) — Vitest + pytest gate, then auto-deploy to Hetzner on green from the tested monorepo SHA. Production infrastructure and deploy code live in [`cloud/`](cloud/); CI runs an immutable support bundle on the VPS. See [`cloud/CLAUDE.md`](cloud/CLAUDE.md) for the deployment contract.

## Runtime and dependencies

- **bun**, not npm — `bun.lock` is canonical for all JS/TS package management.
- Python requirements are pinned to the VPS's `pip freeze`, not the laptop's, because deploys install on the VPS.

## Marketing and creative tooling

| Tool | Role | Where |
|---|---|---|
| Brand system | Enforced design tokens, palette, voice for all UI and assets | `docs/brand-identity.md`, `brand/` |
| Product plates | Anonymized app screenshots used across marketing (captured via chrome-cdp, anonymized before publishing) | `site/public/plates/`, methodology in `site/CLAUDE.md` |
| GooseWorks | AI ad-creative generation (static ads) via MCP; the Radon brand kit there was authored from `docs/brand-identity.md` | External service; gallery at make.gooseworks.ai |
| Remotion | Programmatic promo video (React-based rendering) | `video/` — deliberately untracked; scaffold with `bunx create-video` if absent |

## Retired

- **npm** — replaced by bun; do not regenerate `package-lock.json`.
- **libsql embedded replica** (`data/replica.db`) — decommissioned 2026-05-20 after WAL conflicts; all DB access is direct-to-Turso. Delete the file if it reappears.

## What this file is not

Not a runbook (see `CLAUDE.md`), not operations (see `docs/operations.md`, `docs/cloud-services.md`), not architecture (see `README.md`). If a tool's *rules* changed, update its canonical file; update this map only when a tool is added, replaced, or retired.
