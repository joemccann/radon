# AI subscription auth service: audit + hardening plan (2026-09-18)

Scope: `radon-subscription-tokens` on the app VPS. Providers required: claude
(anthropic), codex, antigravity, grok.

## Audit of what exists (evidence from the live VPS, no secret values read)

| # | Finding | Evidence |
|---|---|---|
| A1 | Refresh grant omits `client_id` for anthropic, codex, gemini. Every refresh is a malformed request. | Bogus-token probe: without `client_id` anthropic/codex/google all answer 400 `invalid_request`; with it they answer `invalid_grant` / 401 `token_expired`. grok (client id in its doc) is the only provider the sidecar ever shows `refreshed`. |
| A2 | Any 400 is classified `needs_reauth`, so A1 pages a browser login that was never needed. | Journal: codex + gemini `needs_reauth [refresh token rejected by the provider]` for days. |
| A3 | Codex expiry guessed as `last_refresh + 1h`; the real access token JWT `exp` is 10 days. The daemon fires the broken refresh ~50 min after every login. | VPS JWT claims: `iat` 2026-09-18, `exp` 2026-09-28. |
| A4 | Antigravity unmanaged. Credential is `~/.gemini/antigravity-cli/antigravity-oauth-token` (`token.expiry`, `token.refresh_token`). | File present on the VPS, zero repo references. |
| A5 | No liveness check. `live` means "the expiry field is in the future", never "the provider accepts it". A revoked or lapsed subscription stays green. | Code: `_evaluate` fresh branch. |
| A6 | No keepalive. Nothing exercises a refresh token the daemon cannot refresh itself. | `cli_refresh_args=None` for every provider. |
| A7 | `grok` and `agy` live in `~/.local/bin`, off the unit PATH, so the CLI-first branch never runs; runbook says they are "absent". | `command -v` on the VPS vs `ls ~/.local/bin`. |
| A8 | Re-auth is "log in elsewhere, copy the file". codex and grok both ship device-code logins that need no file copy. | `codex login --device-auth`, `grok login --device-auth` captured on the VPS. |
| A9 | `claude setup-token` is documented as writing `.credentials.json`; it only prints a token. | Anthropic docs; correct command is `claude auth login`. |
| A10 | gemini file has no consumer (`model_ladder` reads env tokens for gemini) and cannot be refreshed without a Google client secret; it pages every 12h. | grep of `model_ladder.py`. |
| A11 | `_codex_apply` writes a `tokens.expires_at` key codex never wrote into codex's own file. | Code. |

## Plan

- [x] Red tests for every finding
- [x] A1/A2: static public `client_id` per provider; only a dead grant (`invalid_grant`, 401, codex `token_expired` family) is `needs_reauth`; any other 4xx is `error`
- [x] A3/A11: codex expiry from the access-token JWT `exp`; stop writing foreign keys
- [x] A4: `antigravity` provider (CLI-refreshed; no Google client secret in git)
- [x] A5/A6/A7: CLI keepalive probe every 24h (real model call, metered keys scrubbed from the probe env, `~/.local/bin` on the search path); probe is also the CLI-native refresh
- [x] A8: push-driven login. On `needs_reauth` the daemon starts the CLI's own device login, parses URL + code (host allowlist), sends them as the Pushover page (`url`), waits, re-evaluates, seals. `--reauth PROVIDER` for an operator retry
- [x] A9/A10: correct claude command; gemini leaves the default set (adapter kept for `--seal/--restore`)
- [x] Unit timeout + hash, docs, watchdog parity untouched (same service row)
- [x] Wire check (2026-09-18, local real binaries, throwaway HOME): all four probes classify `auth_failed`; child env carries no KEY/TOKEN var; codex + grok login prompts parse on the right host and are killed on deadline; no stray processes
- [ ] Verify: full suite in CI; post-merge `--check --json` on the VPS (the deploy installs the re-hashed unit)
