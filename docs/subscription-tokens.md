# Subscription tokens (`radon-subscription-tokens`)

Four agent CLIs authenticate against the operator's subscriptions rather than
metered API keys: Claude Code (anthropic), OpenAI Codex, xAI Grok, Google
Gemini. Each keeps an OAuth credential file in the `radon` home directory on
the app VPS. `scripts/clients/model_ladder.py` reads those files directly, so a
deleted or expired file silently demotes the whole subscription band to prepaid
keys.

`scripts/subscription_tokens` is the daemon that stops that happening. On every
run it evaluates each provider, refreshes what it can refresh, restores what it
can restore from the vault, and pages only when a human browser login is the
only remaining move.

Timer, unit files and heartbeat row: see [cloud-services.md](cloud-services.md).
Protocol background for the OAuth flows: see
[oauth-subscription-auth.md](oauth-subscription-auth.md).

---

## What the daemon does

1. Resolves each provider's credential file, honouring the provider's own env
   override before the default path.
2. Seals the current known-good bytes into the encrypted secret store
   (`scripts/secret_store.py`) whenever they differ from the vault copy, and
   always before it overwrites a file that parses.
   The value is the exact bytes of the credential file, so a re-seal round
   trips byte for byte and a field Radon does not model is never lost.
3. Refreshes an expiring access token. The provider's own CLI is preferred when
   the binary is installed, because the CLI is the authority on its own file
   format. Otherwise the refresh goes to the provider's token endpoint with
   `grant_type=refresh_token`.
4. Writes the result atomically (temp file in the same directory, `fsync`,
   `os.replace`, mode 0600) and re-seals it.
5. Writes one `service_health` heartbeat under the row `subscription-tokens`
   carrying the worst provider state, plus a per-provider JSON sidecar at
   `/var/lib/radon/subscription-tokens/state.json`.

Registry names in the vault:

    SUBSCRIPTION_TOKEN_ANTHROPIC
    SUBSCRIPTION_TOKEN_CODEX
    SUBSCRIPTION_TOKEN_GROK
    SUBSCRIPTION_TOKEN_GEMINI

The store validates registry names against `^[A-Z][A-Z0-9_]{0,63}$`, so these
are upper case.

---

## States

Evaluated per provider, every run.

| State | Meaning | Pages |
|---|---|---|
| `live` | File present, parses, expiry comfortably in the future. | no |
| `refreshed` | Was expiring or expired, the refresh succeeded, the file was rewritten. | no |
| `restored` | File missing or corrupt, the vault had a copy, it was written back and re-evaluated. | no |
| `unbootstrapped` | No file and no vault copy. Nobody has ever logged this provider in on this host. Not an error. | no |
| `needs_reauth` | A refresh token exists but the provider rejected it, or there is no refresh token at all. | yes |
| `store_unavailable` | The vault could not be opened. Never reported as an empty store. | yes |
| `error` | Anything else: network, exhausted 5xx retries, an unparsable response. | on the 3rd consecutive run |
| `expiring` | `--check` only. The token is missing or past the skew and `--check` changes nothing, so no refresh was attempted. `--once` never reports it. | no |

The aggregate `service_health` state is the worst provider state of the run.

Exit codes: `0` when every provider is `live`, `refreshed`, `restored` or
`unbootstrapped`; `1` when at least one is `needs_reauth`, `error` or
(under `--check`) `expiring`; `78` on
`store_unavailable`.

---

## Operator re-auth runbook

Radon pages with the provider name and the command to run. A page means the
refresh path is exhausted: the daemon cannot complete a browser login on the
operator's behalf, and Radon stores no account passwords.

### anthropic (Claude Code)

```bash
ssh radon@ib-gateway 'claude setup-token'
```

Follow the printed URL in a browser, approve, and paste the code back. This
writes `~/.claude/.credentials.json`. Seal it afterwards (below).

### codex, grok, gemini

The `codex`, `grok` and `gemini` binaries are not installed on the app VPS, and
installing them is out of scope for this daemon. Log in on a machine where the
CLI is installed, using that CLI's own interactive login, then copy the
resulting credential file to the VPS at the path the daemon reads and seal it:

| Provider | Credential file on the VPS | Env override |
|---|---|---|
| anthropic | `~/.claude/.credentials.json` | `CLAUDE_CONFIG_DIR` |
| codex | `~/.codex/auth.json` | `CODEX_HOME` |
| grok | `~/.grok/auth.json` | none |
| gemini | `~/.gemini/oauth_creds.json` | none |

Copy the file with mode 0600 and owner `radon`, then seal. Until a provider has
ever been logged in on this host it reports `unbootstrapped`, which is the
honest answer and does not page.

---

## Sealing a freshly created credential

After any login or manual copy, put the bytes in the vault so the next wipe or
partial write is recoverable without a human. Start the unit: a run seals every
credential file that parses, including one that is still live, so no separate
step is needed.

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

Report without changing anything, in either human or machine form:

```bash
ssh radon@ib-gateway 'cd /home/radon/radon && .venv/bin/python -m scripts.subscription_tokens --check'
ssh radon@ib-gateway 'cd /home/radon/radon && .venv/bin/python -m scripts.subscription_tokens --check --json'
```

`--once` is the mode the timer runs: evaluate and heal every provider, then
exit.

---

## The honest limit

A refresh token that the provider has revoked, or that has itself expired,
cannot be recovered by any daemon. OAuth requires an interactive browser login
to mint a new one. That case is exactly what `needs_reauth` means and exactly
why it pages: one browser login by the operator, then one seal, and the
autonomous path is restored.

Everything short of that is handled without a human: an expired access token, a
deleted file, a truncated file, a fresh home directory after host maintenance.

---

## Secret hygiene

No token, refresh token, client secret or Authorization header value is ever
logged, put in an exception message, written to the sidecar, sent to Pushover,
or recorded in `service_health`. Token material is rendered as
`<redacted len=N>`. The daemon runs as `radon` with `UMask=0077`; the vault is
the existing encrypted secret store, whose key is delivered by systemd
`LoadCredentialEncrypted`, so there is no second crypto system to rotate or
back up.
