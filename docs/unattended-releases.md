# Unattended maintenance releases

The operator has granted standing release authority for completed maintenance
fixes to `com.radon.operator-releases`, a separate operator LaunchAgent. The
nightly machine account remains unable to merge main or reach production. The
controller's installed policy, code, SSH configuration and GitHub login are
outside the bot's writable home. Models receive the diff as data, with no tools,
GitHub token, SSH credentials, user config, repository hooks or project rules.

## Release sequence

Every five minutes the controller first reconciles the current deployed main,
then resumes its durable release journal before considering new work. Eligible branches and authors come from the installed
owner-controlled policy. Discovery rejects non-main targets before even updating
a candidate branch; fresh merge and already-merged recovery reads bind both the
expected head SHA and the main target. Drafts, forks, absent or incomplete checks, cancelled
checks, stale heads, missing independent Sol/Luna approvals, and a production
release still running all defer release. Every expected test shard, both
coverage gates, cloud tests, perimeter smoke, secret scan and PR-body gate must
be present and successful. Other applicable checks must also complete.

Reviews are cached by model, immutable head SHA and complete diff digest.
The controller copies the Homebrew CLI into an owner-only snapshot, verifies
its OpenAI Developer ID signature, and binds review receipts to its SHA-256.
Before each uncached review, a fake-credential local transport probes the same
binary, schema and restricted model catalog and rejects advertised tools.
The GitHub merge API receives that exact head SHA as a compare-and-swap for
the head. Immediately before a new merge request, the controller verifies that
main requires strict up-to-date status checks, enforces protection for
administrators, and retains app-bound checks for every installed-policy required
name. Missing, unreadable or weakened protection defers merging. The controller
never selects an administrator bypass. When strengthening protection, retain
all existing required check names, app IDs and other protection fields.
[GitHub documents strict base freshness and administrator enforcement](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches).

Strict protection rejects an outdated base at the server even if another actor
merges after the controller's final local check; additional SHA reads alone
cannot close that race. This protects the reviewed head against a stale base.
It does not provide a deployment mutex for every other authorized merge actor.
The controller defers its own work while observed main CI/deployment is pending.

The controller waits for successful main CI and deployment, then reconciles both
app and broker root control-plane bundles against that exact main SHA over the
operator's existing SSH access. This runs for externally merged main changes
even with no eligible maintenance PR. Host success requires an installed-SHA
receipt from each host and unchanged GitHub main after both receipts. Complete
receipts persist in the private WAL and avoid repeated SSH for unchanged main
across restarts. A partial host failure or moving main records no new success
and retries reconciliation before another controller merge.
No bot-controlled directory or mutable release marker becomes root authority.

P1 fixes are exported to `validation/fix/<base>/<head>` in the existing private
security-report repository. The controller verifies that repository remains
private, opens private CI validation, and obtains both independent reviews.
Only after rechecking CI and refs does it publish the identical Git objects as
a public maintenance PR. Public CI and the ordinary release sequence still
apply. P0 findings remain under explicit incident coordination. Source contents
and raw findings never belong in public controller status or notifications.

## Installed files and recovery

`scripts/install_unattended_release.py` installs immutable code under
`~/.local/share/radon-operator-releases/releases`, preserves owner policy and
credentials across refreshes, and registers the existing-user LaunchAgent.
`hosts.json` names the operator SSH aliases; no production key is copied into
the bot account or the repository. Only `PUSHOVER_USER` and `PUSHOVER_TOKEN` are
copied to its mode-0600 notification file.

After successful production deployment, the controller refreshes only its
fixed Python module allowlist and the installed operator updater's code from
that immutable main SHA. Downloaded blobs must match their Git object hashes
and parse as Python before atomic activation for the next tick. Existing
release policy, SSH aliases, notification credentials and updater policy remain
separate from these code refreshes.

The state directory holds `findings.jsonl`, `state.json` and `SUMMARY.md`.
An exclusive controller lock serializes runs. WAL writes are fsynced before
atomic snapshots. Merge status is queried after an interruption; claimed heads
are revalidated before a new merge attempt. SHA-bound host sync is repeatable.

Pushover cannot provide exactly-once delivery across a lost response. The
controller claims notification attempts durably before sending, records accepted
responses, and quarantines uncertain attempts instead of blindly duplicating
an accepted page. Release completion is represented by CI and host receipts,
not by notification delivery. The nightly loop retains its PR-green page; the
operator controller sends a separate release-verification page.

## Toolchain and root-owned runner updates

See [Nightly toolchain updates](nightly-toolchain-updates.md). The existing
operator updater manages each application's update separately. DeepSec repairs
its private dependencies before source review on a normal nightly fire, using
the bot's existing subscription login and a real SDK inference smoke test.
Missing authentication remains an explicit incomplete run; credentials are never
invented, copied across accounts or replaced with a billing API key.

The root-owned runner uses a shared lifecycle lock. Scheduled root refreshes
accept only public main with completed successful production CI, take the
exclusive maintenance lock, stage root-owned source and reinstall all seven
cooperative loop wrappers. A partial installation does not publish the readiness
marker. No job updates a CLI underneath another job using it.

Installing this root updater on an existing Mac requires one legitimate macOS
administrator authorization. There is no passwordless root bridge in the old
runner; adding one through a bot-writable path would recreate the vulnerability
being fixed. The rootless release controller and DeepSec preflight do not depend
on that bootstrap. Subsequent reviewed main updates need no terminal command.
