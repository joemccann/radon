# Subscription tokens (`radon-subscription-tokens`)

Four agent CLIs authenticate against the operator's subscriptions rather than
metered API keys: Claude Code (anthropic), OpenAI Codex, xAI Grok and Google
Antigravity (`agy`). Each keeps an OAuth credential file in the `radon` home
directory on the app VPS. `scripts/clients/model_ladder.py` reads the anthropic,
codex and grok files directly, so a deleted or expired file silently demotes the
whole subscription band to prepaid keys.

`scripts/subscription_tokens` is the daemon that stops that happening. On every
run it evaluates each provider, refreshes what it can refresh, restores what it
can restore from the vault, proves each login with a real model call once a day,
and when a grant is truly dead it starts the CLI's own login and pages the
operator the link.

Timer, unit files and heartbeat row: see [cloud-services.md](cloud-services.md).
Protocol background for the OAuth flows: see
[oauth-subscription-auth.md](oauth-subscription-auth.md).

---

## Per-provider contract

Every row was verified against the real binary on the VPS, run against a
throwaway home so no live credential was touched.

| Provider | Credential file | Expiry read from | Refresh path | Keepalive probe | Re-auth |
|---|---|---|---|---|---|
| anthropic | `~/.claude/.credentials.json` (`CLAUDE_CONFIG_DIR`) | `claudeAiOauth.expiresAt` | `claude -p`, then token endpoint with the public client id | `claude -p ... --max-turns 1` | paste-code: `claude auth login` over SSH |
| codex | `~/.codex/auth.json` (`CODEX_HOME`) | `exp` claim of the access-token JWT (ten days) | `codex exec`, then token endpoint with the public client id | `codex exec --skip-git-repo-check ...` | **push**: device code |
| grok | `~/.grok/auth.json` | `expires_at` (earliest entry) | `grok -p`, then OIDC-discovered token endpoint | `grok -p ...` | **push**: device code |
| antigravity | `~/.gemini/antigravity-cli/antigravity-oauth-token` | `token.expiry` | `agy -p` only | `agy -p ... --output-format json` | **push**: Google consent link (60s window) |

`grok` and `agy` install to `~/.local/bin`, which systemd's PATH does not carry.
The daemon searches `~/.local/bin` and `~/.grok/bin` itself.

`gemini` (`~/.gemini/oauth_creds.json`) left the scheduled set on 2026-09-18.
Nothing reads that file (the ladder's gemini rung reads env tokens), Google
folded the Gemini CLI subscription login into Antigravity, and a Google
installed-app refresh grant needs a client secret that must never be in this
public repo. The adapter stays for `--seal gemini` / `--restore gemini`.

---

## What the daemon does

1. Resolves each provider's credential file, honouring the provider's own env
   override before the default path.
2. Seals the current known-good bytes into the encrypted secret store
   (`scripts/secret_store.py`) whenever they differ from the vault copy, and
   always before it overwrites a file that parses.
   The value is the exact bytes of the credential file, so a re-seal round
   trips byte for byte and a field Radon does not model is never lost.
3. Refreshes an expiring access token. The provider's own CLI goes first,
   because the CLI is the authority on its own file format: one real model call
   makes it refresh its own file. Otherwise the refresh goes to the provider's
   token endpoint with `grant_type=refresh_token` and the provider's public
   OAuth `client_id`. The anthropic, OpenAI and Google endpoints all answer
   `400 invalid_request` without one.
4. Writes the result atomically (temp file in the same directory, `fsync`,
   `os.replace`, mode 0600) and re-seals it. It never adds a key the vendor did
   not write into the vendor's own file.
5. **Keepalive.** Once per `KEEPALIVE_INTERVAL` (24h) a credential that looks
   live is proven with one real model call ("Reply with the single word ok").
   An expiry field in the future says nothing about a revoked grant or a lapsed
   subscription, and an unexercised refresh token is what goes stale: Codex's
   own guidance is a weekly exercise, Google retires a refresh token after six
   idle months, grok defaults to a 30-day credential. Daily is inside all three.
6. Writes one `service_health` heartbeat under the row `subscription-tokens`
   carrying the worst provider state, plus a per-provider JSON sidecar at
   `/var/lib/radon/subscription-tokens/state.json` (state, expiry,
   `last_refresh_at`, `last_probe_at`, error streak, page cooldowns).

### The CLI environment is an allowlist

`/etc/radon/env` carries `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` and
`XAI_API_KEY`. Each one **outranks** the subscription login inside its CLI, so a
probe that inherited them would bill the metered key and prove nothing about the
login. It also carries every other Radon secret. A provider CLI therefore runs
with `HOME`, a `PATH`, locale, proxy and XDG variables plus that provider's own
config-dir override, and nothing else. It runs in an empty temporary directory,
in its own process group, with stdin closed, and is killed as a group on timeout.

Registry names in the vault:

    SUBSCRIPTION_TOKEN_ANTHROPIC
    SUBSCRIPTION_TOKEN_CODEX
    SUBSCRIPTION_TOKEN_GROK
    SUBSCRIPTION_TOKEN_ANTIGRAVITY
    SUBSCRIPTION_TOKEN_GEMINI   (legacy, unscheduled)

The store validates registry names against `^[A-Z][A-Z0-9_]{0,63}$`, so these
are upper case.

---

## States

Evaluated per provider, every run.

| State | Meaning | Pages |
|---|---|---|
| `live` | File present and parses, and either the expiry is comfortably in the future or (antigravity) a refresh token is present and only `agy` reads the access token. Proven by the keepalive probe once a day. | no |
| `refreshed` | Was expiring or expired, the refresh succeeded, the file was rewritten. | no |
| `restored` | File missing or corrupt, the vault had a copy, it was written back and re-evaluated. | no |
| `unbootstrapped` | No file and no vault copy. Nobody has ever logged this provider in on this host. Not an error. | no |
| `needs_reauth` | The grant is dead: the token endpoint answered `invalid_grant` / 401 / a `token_expired` family code, the keepalive probe got the CLI's own "not logged in" answer, or there is no refresh token at all. | yes, with a login link when the provider has a push login |
| `store_unavailable` | The vault could not be opened. Never reported as an empty store. | yes |
| `error` | Anything else: network, exhausted 5xx retries, an unparsable response, **or a 4xx that is not a dead grant** (a malformed request is a bug here, not the operator's problem). | on the 3rd consecutive run |
| `expiring` | `--check` only. The token is missing or past the skew and `--check` changes nothing, so no refresh was attempted. `--once` never reports it. | no |

A keepalive probe that fails for a reason other than the login (a usage cap, a
vendor outage, a timeout) is **inconclusive**: the provider stays `live`,
`last_error` says `keepalive probe inconclusive`, `last_probe_at` is not
stamped, and the next run asks again. Whatever the probe reported, the file is
re-read and re-sealed afterwards, because a CLI can rotate its refresh token and
then fail the model call. A login that every probe has left unproven for
`KEEPALIVE_UNPROVEN_LIMIT` (72h, tracked as `unproven_since` in the sidecar)
becomes `error`: a vendor rewording its auth message must not read as `live`
forever. The probe only runs where the provider's CLI is installed.

Two things that look like a dead login and are not, both `error`: OIDC discovery
not answering (an issuer outage), and a token endpoint rejecting the **client**
(`invalid_client` / `unauthorized_client`, fixed in the provider table, never by
logging in again). A dead grant is never presented a second time.

The aggregate `service_health` state is the worst provider state of the run.

Exit codes: `0` when every provider is `live`, `refreshed`, `restored` or
`unbootstrapped`; `1` when at least one is `needs_reauth`, `error` or
(under `--check`) `expiring`; `78` on
`store_unavailable`.

---

## Operator re-auth runbook

### codex, grok, antigravity: tap the link

When one of these reports `needs_reauth`, the daemon starts the CLI's own login
on the VPS (`codex login --device-auth`, `grok login --device-auth`, or `agy`),
reads the sign-in link and one-time code out of its output, and sends them as
the Pushover page. Tap the link on any device, sign in, enter the code if one is
shown. The CLI on the VPS writes its own credential file, the daemon
re-evaluates the provider, probes it, and seals it. No SSH, no file copy.

- The link is only ever a URL on the provider's own login host
  (`auth.openai.com`, `accounts.x.ai`, `accounts.google.com`). CLI output is not
  a trusted source of links for the operator's phone.
- One login per timer run, held for at most 10 minutes. The link is sent once
  per 12h page cooldown. Codex and grok codes last about 15 minutes. A second
  dead provider is not paged that run; it gets the login slot on the next one.
- **Antigravity's window is 60 seconds.** `agy` waits that long for Google's
  hosted callback. Miss it and retry with `--reauth` (below).
- Codex device login must be switched on once in ChatGPT security settings
  (or by the workspace admin).
- A one-time device code is not token material: it is useless without the
  operator's own signed-in approval, and it only ever authorises this VPS.

Retry on demand, ignoring the cooldown. This needs no vault, so a bare shell is
fine; the next timer run seals the new file:

```bash
ssh radon@ib-gateway 'cd /home/radon/radon && .venv/bin/python -m scripts.subscription_tokens --reauth codex'
```

Every link page also carries the interactive fallback:

```bash
ssh -t radon@ib-gateway 'codex login --device-auth'
ssh -t radon@ib-gateway '~/.local/bin/grok login --device-auth'
ssh -t radon@ib-gateway '~/.local/bin/agy'
```

### anthropic (Claude Code): paste-code over SSH

```bash
ssh -t radon@ib-gateway 'claude auth login'
```

Follow the printed URL in a browser, approve, and paste the code back. This
writes `~/.claude/.credentials.json`. Claude's login wants the code typed back
into the CLI, which no push can do, so this is the one provider that still needs
a terminal. With the refresh grant fixed and the daily keepalive it should be
rare.

`claude setup-token` is **not** this. It prints a one-year token for
`CLAUDE_CODE_OAUTH_TOKEN` and writes no file.

Until a provider has ever been logged in on this host it reports
`unbootstrapped`, which is the honest answer and does not page.

---

## Sealing a freshly created credential

After any login, put the bytes in the vault so the next wipe or partial write is
recoverable without a human. A push login seals by itself. Otherwise start the
unit: a run seals every credential file that parses, including one that is still
live, so no separate step is needed.

```bash
ssh radon@ib-gateway 'sudo systemctl start radon-subscription-tokens.service'
ssh radon@ib-gateway 'systemctl status radon-subscription-tokens.service'
```

⛔ Run the seal through the unit, not by hand. The store key reaches the daemon
as a systemd credential (`LoadCredentialEncrypted`). A bare shell has no such
credential, so `scripts/secret_store.py` falls back to
`~/.radon/secret_store.key`, auto-generates one on first use, and writes
ciphertext the unit can never decrypt. The explicit form exists for that same
unit context:

```bash
ssh radon@ib-gateway 'cd /home/radon/radon && .venv/bin/python -m scripts.subscription_tokens --seal anthropic'
```

The inverse, for a host that lost the file outside a scheduled run:

```bash
ssh radon@ib-gateway 'cd /home/radon/radon && .venv/bin/python -m scripts.subscription_tokens --restore anthropic'
```

`--restore` refuses when the file already on disk is newer than the vault copy,
or does not parse, and exits 1 saying so. That is the case where you have just
re-authenticated: seal the new file instead. Only pass `--force` when you mean
to discard the on-disk credential and install the vault copy over it.

Every mode takes an exclusive lock on `/run/lock/radon-subscription-tokens.lock`,
so a manual command that lands while the 30-minute timer is running prints
`skipped: another run holds the lock` and exits 0. Re-run it.

Report without changing anything, in either human or machine form. `--check`
never refreshes, probes, logs in or pages:

```bash
ssh radon@ib-gateway 'cd /home/radon/radon && .venv/bin/python -m scripts.subscription_tokens --check'
ssh radon@ib-gateway 'cd /home/radon/radon && .venv/bin/python -m scripts.subscription_tokens --check --json'
```

`--once` is the mode the timer runs: evaluate, heal and (when due) probe every
provider, then exit. It starts no CLI work it cannot finish inside
`RUN_BUDGET_SECONDS` (1500s), under the unit's `TimeoutStartSec=1700`, so a slow
run still ends with a sidecar and a heartbeat.

---

## The honest limit

A refresh token that the provider has revoked, or that has itself expired,
cannot be recovered by any daemon. OAuth requires an interactive browser login
to mint a new one. That case is exactly what `needs_reauth` means. For codex,
grok and antigravity the daemon reduces it to one tap on a page; for claude it
is one SSH command.

Everything short of that is handled without a human: an expired access token, a
deleted file, a truncated file, a fresh home directory after host maintenance, a
grant that would otherwise have gone stale from disuse.

---

## Secret hygiene

No token, refresh token, client secret or Authorization header value is ever
logged, put in an exception message, written to the sidecar, sent to Pushover,
or recorded in `service_health`. Token material is rendered as
`<redacted len=N>`. CLI output is classified and discarded, never logged. The
OAuth client ids in the provider table are the vendors' published public-client
ids, not secrets. The daemon runs as `radon` with `UMask=0077`; the vault is
the existing encrypted secret store, whose key is delivered by systemd
`LoadCredentialEncrypted`, so there is no second crypto system to rotate or
back up.
